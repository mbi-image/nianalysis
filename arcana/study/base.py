from past.builtins import basestring
from builtins import object
from itertools import chain
import sys
import os.path as op
import types
from logging import getLogger
from nipype.interfaces.utility import IdentityInterface
from arcana.exceptions import (
    ArcanaMissingDataException, ArcanaNameError, ArcanaUsageError,
    ArcanaMissingInputError, ArcanaNoConverterError, ArcanaDesignError,
    ArcanaCantPickleStudyError)
from .pipeline import Pipeline
from arcana.data import BaseData
from nipype.pipeline import engine as pe
from .parameter import Parameter, SwitchSpec
from arcana.repository.interfaces import RepositorySource
from arcana.repository import DirectoryRepository
from arcana.processor import LinearProcessor
from arcana.environment import StaticEnvironment

logger = getLogger('arcana')


class Study(object):
    """
    Abstract base study class from which all study derive.

    Parameters
    ----------
    name : str
        The name of the study.
    repository : Repository
        An Repository object that provides access to a DaRIS, XNAT or local file
        system
    processor : Processor
        A Processor to process the pipelines required to generate the
        requested derived filesets.
    inputs : Dict[str, FilesetSelector | FilesetSpec | FieldSelector | FieldSpec] | List[FilesetSelector | FilesetSpec | FieldSelector | FieldSpec]
        Either a list or a dictionary containing FilesetSelector,
        FieldSelector, FilesetSpec, or FieldSpec objects, which specify the
        names of input filesets to the study, i.e. those that won't
        be generated by this study (although can be derived by the parent
        MultiStudy)
    environment : Environment
        An Environment within which to process the pipelines. Handles the
        version management + loading/unloading of software requirements
    parameters : List[Parameter] | Dict[str, (int|float|str)]
        Parameters that are passed to pipelines when they are constructed
        either as a dictionary of key-value pairs or as a list of
        'Parameter' objects. The name and dtype must match ParameterSpecs in
        the _param_spec class attribute (see 'add_param_specs').
    subject_ids : List[(int|str)]
        List of subject IDs to restrict the analysis to
    visit_ids : List[(int|str)]
        List of visit IDs to restrict the analysis to
    enforce_inputs : bool (default: True)
        Whether to check the inputs to see if any acquired filesets
        are missing
    reprocess : bool
        Whether to reprocess fileset|fields that have been created with
        different parameters and/or pipeline-versions. If False then
        and exception will be thrown if the repository already contains
        matching filesets|fields created with different parameters.
    fill_tree : bool
        Whether to fill the tree of the destination repository with the
        provided subject and/or visit IDs. Intended to be used when the
        destination repository doesn't contain any of the the input
        filesets/fields (which are stored in external repositories) and
        so the sessions will need to be created in the destination
        repository.


    Class Attrs
    -----------
    add_data_specs : List[FilesetSpec|FieldSpec]
        Adds specs to the '_data_specs' class attribute,
        which is a dictionary that maps the names of filesets that are
        used and generated by the study to (Fileset|Field)Spec objects.
    add_param_specs : List[ParameterSpec]
        Adds specs to the '_param_specs' class attribute,
        which is a dictionary that maps the names of parameters that are
        provided to pipelines in the study
    """

    _data_specs = {}
    _param_specs = {}

    implicit_cls_attrs = ['_data_specs', '_param_specs']

    SUBJECT_ID = 'subject_id'
    VISIT_ID = 'visit_id'
    ITERFIELDS = (SUBJECT_ID, VISIT_ID)
    FREQUENCIES = {
        'per_study': (),
        'per_subject': (SUBJECT_ID,),
        'per_visit': (VISIT_ID,),
        'per_session': (SUBJECT_ID, VISIT_ID)}

    def __init__(self, name, repository, processor, inputs,
                 environment=None, parameters=None, subject_ids=None,
                 visit_ids=None, enforce_inputs=True, reprocess=False,
                 fill_tree=False):
        try:
            # This works for PY3 as the metaclass inserts it itself if
            # it isn't provided
            metaclass = type(self).__dict__['__metaclass__']
            if not issubclass(metaclass, StudyMetaClass):
                raise KeyError
        except KeyError:
            raise ArcanaUsageError(
                "Need to have StudyMetaClass (or a sub-class) as "
                "the metaclass of all classes derived from Study")
        if isinstance(repository, basestring):
            repository = DirectoryRepository(repository, depth=None)
        if isinstance(processor, basestring):
            processor = LinearProcessor(processor)
        if environment is None:
            environment = StaticEnvironment()
        self._name = name
        self._repository = repository
        self._processor = processor.bind(self)
        self._environment = environment
        self._inputs = {}
        self._subject_ids = subject_ids
        self._visit_ids = visit_ids
        self._fill_tree = fill_tree
        if not self.subject_ids:
            raise ArcanaUsageError(
                "No subject IDs provided and destination repository "
                "is empty")
        if not self.visit_ids:
            raise ArcanaUsageError(
                "No visit IDs provided and destination repository "
                "is empty")
        self._reprocess = reprocess
        # For recording which parameters are accessed
        # during pipeline generation so they can be attributed to the
        # pipeline after it is generated (and then saved in the
        # provenance
        self._pipeline_to_generate = None
        self._referenced_parameters = None
        # Set parameters
        if parameters is None:
            parameters = {}
        elif not isinstance(parameters, dict):
            # Convert list of parameters into dictionary
            parameters = {o.name: o for o in parameters}
        self._parameters = {}
        for param_name, param in list(parameters.items()):
            if not isinstance(param, Parameter):
                param = Parameter(param_name, param)
            try:
                param_spec = self._param_specs[param_name]
            except KeyError:
                raise ArcanaNameError(
                    param_name,
                    "Provided parameter '{}' is not present in the "
                    "allowable parameters for {} classes ('{}')"
                    .format(param_name, type(self).__name__,
                            "', '".join(self.parameter_spec_names())))
            param_spec.check_valid(param, context='{}(name={})'.format(
                type(self).__name__, name))
            self._parameters[param_name] = param
        # Convert inputs to a dictionary if passed in as a list/tuple
        if not isinstance(inputs, dict):
            inputs = {i.name: i for i in inputs}
        # Add each "input fileset" checking to see whether the given
        # fileset_spec name is valid for the study types
        for inpt_name, inpt in list(inputs.items()):
            try:
                spec = self.data_spec(inpt_name)
            except ArcanaNameError:
                raise ArcanaNameError(
                    inpt.name,
                    "Input name '{}' isn't in data specs of {} ('{}')"
                    .format(
                        inpt.name, self.__class__.__name__,
                        "', '".join(self._data_specs)))
            else:
                if spec.is_fileset:
                    if inpt.is_field:
                        raise ArcanaUsageError(
                            "Passed field ({}) as input to fileset spec"
                            " {}".format(inpt, spec))
                    if spec.derived:
                        try:
                            # FIXME: should provide requirement manager to
                            # converter_from but it hasn't been implemented yet
                            spec.format.converter_from(inpt.format)
                        except ArcanaNoConverterError as e:
                            raise ArcanaNoConverterError(
                                "{}, which is requried to convert:\n{} "
                                "to\n{}.".format(e, inpt, spec))
                    else:
                        if inpt.format not in spec.valid_formats:
                            raise ArcanaUsageError(
                                "Cannot pass {} as an input to {} as it is "
                                "not in one of the valid formats ('{}')"
                                .format(
                                    inpt, spec,
                                    "', '".join(
                                        f.name for f in spec.valid_formats)))
                elif not inpt.is_field:
                    raise ArcanaUsageError(
                        "Passed fileset ({}) as input to field spec {}"
                        .format(inpt, spec))
            self._inputs[inpt_name] = inpt.bind(self)
        # "Bind" data specs in the class to the current study object
        # this will allow them to prepend the study name to the name
        # of the fileset
        self._bound_specs = {}
        for spec in self.data_specs():
            if spec.name not in self.input_names:
                if not spec.derived and spec.default is None:
                    # Emit a warning if an acquired fileset has not been
                    # provided for an "acquired fileset"
                    msg = (" acquired fileset '{}' was not given as"
                           " an input of {}.".format(spec.name, self))
                    if spec.optional:
                        logger.info('Optional' + msg)
                    else:
                        if enforce_inputs:
                            raise ArcanaMissingInputError(
                                'Non-optional' + msg + " Pipelines "
                                "depending on this fileset will not "
                                "run")

    def __repr__(self):
        """String representation of the study"""
        return "{}(name='{}')".format(self.__class__.__name__,
                                      self.name)

    def __reduce__(self):
        """
        Control how study classes are pickled to allow some generated
        classes (those that don't define additional methods) to be
        generated
        """
        cls = type(self)
        module = sys.modules[cls.__module__]
        try:
            # Check whether the study class is generated or not by
            # seeing if it exists in its module
            if cls is not getattr(module, cls.__name__):
                raise AttributeError
        except AttributeError:
            cls_dct = {}
            for name, attr in list(cls.__dict__.items()):
                if isinstance(attr, types.FunctionType):
                    try:
                        if not attr.auto_added:
                            raise ArcanaCantPickleStudyError()
                    except (AttributeError, ArcanaCantPickleStudyError):
                        raise ArcanaCantPickleStudyError(
                            "Cannot pickle auto-generated study class "
                            "as it contains non-auto-added method "
                            "{}:{}".format(name, attr))
                elif name not in self.implicit_cls_attrs:
                    cls_dct[name] = attr
            pkld = (pickle_reconstructor,
                    (cls.__metaclass__, cls.__name__, cls.__bases__,
                     cls_dct), self.__dict__)
        else:
            # Use standard pickling if not a generated class
            pkld = object.__reduce__(self)
        return pkld

    @property
    def tree(self):
        return self.repository.cached_tree(
            subject_ids=self._subject_ids,
            visit_ids=self._visit_ids,
            fill=self._fill_tree)

    def clear_binds(self):
        """
        Called after a pipeline is run against the study to force an update of
        the derivatives that are now present in the repository if a subsequent
        pipeline is run.
        """
        self.repository.clear_cache()
        self._bound_specs = {}

    @property
    def processor(self):
        return self._processor

    @property
    def environment(self):
        return self._environment

    @property
    def inputs(self):
        return list(self._inputs.values())

    @property
    def input_names(self):
        return list(self._inputs.keys())

    def input(self, name):
        try:
            return self._inputs[name]
        except KeyError:
            raise ArcanaNameError(
                name,
                "{} doesn't have an input named '{}'"
                .format(self, name))

    @property
    def missing_inputs(self):
        return (n for n in self.acquired_data_spec_names()
                if n not in self._inputs)

    @property
    def subject_ids(self):
        if self._subject_ids is None:
            return [s.id for s in self.tree.subjects]
        return self._subject_ids

    @property
    def visit_ids(self):
        if self._visit_ids is None:
            return [v.id for v in self.tree.visits]
        return self._visit_ids

    @property
    def prefix(self):
        """The study name as a prefix for fileset names"""
        return self.name + '_'

    @property
    def name(self):
        """Accessor for the unique study name"""
        return self._name

    @property
    def reprocess(self):
        return self._reprocess

    @property
    def repository(self):
        "Accessor for the repository member (e.g. Daris, XNAT, MyTardis)"
        return self._repository

    def pipeline(self, *args, **kwargs):
        """
        Creates a Pipeline object, passing the study (self) as the first
        argument
        """
        return Pipeline(self, *args, **kwargs)

    def _get_parameter(self, name):
        try:
            parameter = self._parameters[name]
        except KeyError:
            try:
                parameter = self._param_specs[name]
            except KeyError:
                raise ArcanaNameError(
                    name,
                    "Invalid parameter, '{}', in {} (valid '{}')"
                    .format(
                        name, self._param_error_location,
                        "', '".join(self.parameter_spec_names())))
        return parameter

    def parameter(self, name):
        """
        Retrieves the value of the parameter and registers the parameter
        as being used by this pipeline for use in provenance capture

        Parameters
        ----------
        name : str
            The name of the parameter to retrieve
        """
        if self._referenced_parameters is not None:
            self._referenced_parameters.add(name)
        return self._get_parameter(name).value

    def branch(self, name, values=None):  # @UnusedVariable @IgnorePep8
        """
        Checks whether the given switch matches the value provided

        Parameters
        ----------
        name : str
            The name of the parameter to retrieve
        value : str | None
            The value(s) of the switch to match if a non-boolean switch
        """
        if isinstance(values, basestring):
            values = [values]
        spec = self.parameter_spec(name)
        if not isinstance(spec, SwitchSpec):
            raise ArcanaUsageError(
                "{} is standard parameter not a switch".format(spec))
        switch = self._get_parameter(name)
        if spec.is_boolean:
            if values is not None:
                raise ArcanaDesignError(
                    "Should not provide values ({}) to boolean switch "
                    "'{}' in {}".format(
                        values, name, self._param_error_location))
            in_branch = switch.value
        else:
            if values is None:
                raise ArcanaDesignError(
                    "Value(s) need(s) to be provided non-boolean switch"
                    " '{}' in {}".format(
                        name, self._param_error_location))
            # Register parameter as being used by the pipeline
            unrecognised_values = set(values) - set(spec.choices)
            if unrecognised_values:
                raise ArcanaDesignError(
                    "Provided value(s) ('{}') for switch '{}' in {} "
                    "is not a valid option ('{}')".format(
                        "', '".join(unrecognised_values), name,
                        self._param_error_location,
                        "', '".join(spec.choices)))
            in_branch = switch.value in values
        if self._referenced_parameters is not None:
            self._referenced_parameters.add(name)
        return in_branch

    def unhandled_branch(self, name):
        """
        Convenient method for raising exception if a pipeline doesn't
        handle a particular switch value

        Parameters
        ----------
        name : str
            Name of the switch
        value : str
            Value of the switch which hasn't been handled
        """
        raise ArcanaDesignError(
            "'{}' value of '{}' switch in {} is not handled"
            .format(self._get_parameter(name), name,
                    self._param_error_location))

    @property
    def _param_error_location(self):
        return ("generation of '{}' pipeline of {}"
                .format(self._pipeline_to_generate, self))

    @property
    def parameters(self):
        for param_name in self._param_specs:
            yield self._get_parameter(param_name)

    @property
    def switches(self):
        for name in self._param_specs:
            if isinstance(self.spec(name), SwitchSpec):
                yield self._get_parameter(name)

    def data(self, name, subject_id=None, visit_id=None, **kwargs):
        """
        Returns the Fileset or Field associated with the name,
        generating derived filesets as required. Multiple names in a
        list can be provided, in which case their workflows are
        joined into a single workflow.

        Parameters
        ----------
        name : str | List[str]
            The name of the FilesetSpec|FieldSpec to retried the
            filesets for
        subject_id : int | str | List[int|str] | None
            The subject ID or subject IDs to return. If None all are
            returned
        visit_id : int | str | List[int|str] | None
            The visit ID or visit IDs to return. If None all are
            returned

        Returns
        -------
        data : Fileset | Field | List[Fileset | Field] | List[List[Fileset | Field]]
            If a single name is provided then data is either a single
            Fileset or field if a single subject_id and visit_id are
            provided, otherwise a list of filesets or fields
            corresponding to the given name. If muliple names are
            provided then a list is returned containing the data for
            each provided name.
        """
        if isinstance(name, basestring):
            single_name = True
            names = [name]
        else:
            names = name
            single_name = False
        def is_single_id(id_):  # @IgnorePep8
            return isinstance(id_, (basestring, int))
        subject_ids = ([subject_id]
                       if is_single_id(subject_id) else subject_id)
        visit_ids = ([visit_id] if is_single_id(visit_id) else visit_id)
        # Work out which pipelines need to be run
        pipelines = []
        for name in names:
            try:
                pipeline = self.spec(name).pipeline
                pipeline.required_outputs.add(name)
                pipelines.append(pipeline)
            except AttributeError:
                pass  # Match objects don't have pipelines
        # Run all pipelines together
        if pipelines:
            self.processor.run(
                *pipelines, subject_ids=subject_ids,
                visit_ids=visit_ids, **kwargs)
        all_data = []
        for name in names:
            spec = self.spec(name)
            data = spec.collection
            for item in data:
                item._exists = True
            if subject_ids is not None and spec.frequency in (
                    'per_session', 'per_subject'):
                data = [d for d in data if d.subject_id in subject_ids]
            if visit_ids is not None and spec.frequency in (
                    'per_session', 'per_visit'):
                data = [d for d in data if d.visit_id in visit_ids]
            if not data:
                raise ArcanaUsageError(
                    "No matching data found (subject_id={}, visit_id={})"
                    .format(subject_id, visit_id))
            if is_single_id(subject_id) and is_single_id(visit_id):
                assert len(data) == 1
                data = data[0]
            else:
                data = spec.CollectionClass(spec.name, data)
            if single_name:
                return data
            all_data.append(data)
        return all_data

    def save_workflow_graph_for(self, spec_name, fname, full=False,
                                style='flat', **kwargs):
        """
        Saves a graph of the workflow to generate the requested spec_name

        Parameters
        ----------
        spec_name : str
            Name of the spec to generate the graph for
        fname : str
            The filename for the saved graph
        style : str
            The style of the graph, can be one of can be one of
            'orig', 'flat', 'exec', 'hierarchical'
        """
        pipeline = self.spec(spec_name).pipeline
        if full:
            workflow = pe.Workflow(name='{}_gen'.format(spec_name),
                                   base_dir=self.processor.work_dir)
            self.processor._connect_pipeline(
                pipeline, workflow, **kwargs)
        else:
            workflow = pipeline._workflow
        fname = op.expanduser(fname)
        if not fname.endswith('.png'):
            fname += '.png'
        dotfilename = fname[:-4] + '.dot'
        workflow.write_graph(graph2use=style,
                             dotfilename=dotfilename)

    def spec(self, name):
        """
        Returns either the input corresponding to a fileset or field
        field spec or a spec or parameter that has either
        been passed to the study as an input or can be derived.

        Parameters
        ----------
        name : Str | BaseData | Parameter
            A parameter, fileset or field or name of one
        """
        # If the provided "name" is actually a data item or parameter then
        # replace it with its name.
        if isinstance(name, (BaseData, Parameter)):
            name = name.name
        # If name is a parameter than return the parameter spec
        if name in self._param_specs:
            return self._param_specs[name]
        else:
            return self.bound_spec(name)

    def bound_spec(self, name):
        """
        Returns an input selector or derived spec bound to the study, i.e.
        where the repository tree is checked for existing outputs

        Parameters
        ----------
        name : Str
            A name of a fileset or field
        """
        # If the provided "name" is actually a data item or parameter then
        # replace it with its name.
        if isinstance(name, BaseData):
            name = name.name
        # Get the spec from the class
        spec = self.data_spec(name)
        try:
            bound = self._inputs[name]
        except KeyError:
            if not spec.derived and spec.default is None:
                raise ArcanaMissingDataException(
                    "Acquired (i.e. non-generated) fileset '{}' "
                    "was not supplied when the study '{}' was "
                    "initiated".format(name, self.name))
            else:
                try:
                    bound = self._bound_specs[name]
                except KeyError:
                    bound = self._bound_specs[name] = spec.bind(self)
        return bound

    @classmethod
    def data_spec(cls, name):
        """
        Return the fileset_spec, i.e. the template of the fileset expected to
        be supplied or generated corresponding to the fileset_spec name.

        Parameters
        ----------
        name : Str
            Name of the fileset_spec to return
        """
        # If the provided "name" is actually a data item or parameter then
        # replace it with its name.
        if isinstance(name, BaseData):
            name = name.name
        try:
            return cls._data_specs[name]
        except KeyError:
            raise ArcanaNameError(
                name,
                "No fileset spec named '{}' in {} (available: "
                "'{}')".format(name, cls.__name__,
                               "', '".join(list(cls._data_specs.keys()))))

    @classmethod
    def parameter_spec(cls, name):
        try:
            return cls._param_specs[name]
        except KeyError:
            raise ArcanaNameError(
                name,
                "No parameter spec named '{}' in {} (available: "
                "'{}')".format(name, cls.__name__,
                               "', '".join(list(cls._param_specs.keys()))))

    @classmethod
    def data_specs(cls):
        """Lists all data_specs defined in the study class"""
        return iter(cls._data_specs.values())

    @classmethod
    def parameter_specs(cls):
        return iter(cls._param_specs.values())

    @classmethod
    def data_spec_names(cls):
        """Lists the names of all data_specs defined in the study"""
        return iter(cls._data_specs.keys())

    @classmethod
    def parameter_spec_names(cls):
        """Lists the names of all parameter_specs defined in the study"""
        return iter(cls._param_specs.keys())

    @classmethod
    def spec_names(cls):
        return chain(cls.data_spec_names(),
                     cls.parameter_spec_names())

    @classmethod
    def acquired_data_specs(cls):
        """
        Lists all data_specs defined in the study class that are
        provided as inputs to the study
        """
        return (c for c in cls.data_specs() if not c.derived)

    @classmethod
    def derived_data_specs(cls):
        """
        Lists all data_specs defined in the study class that are typically
        generated from other data_specs (but can be overridden by input
        filesets)
        """
        return (c for c in cls.data_specs() if c.derived)

    @classmethod
    def derived_data_spec_names(cls):
        """Lists the names of generated data_specs defined in the study"""
        return (c.name for c in cls.derived_data_specs())

    @classmethod
    def acquired_data_spec_names(cls):
        "Lists the names of acquired data_specs defined in the study"
        return (c.name for c in cls.acquired_data_specs())

    def cache_inputs(self):
        """
        Runs the Study's repository source node for each of the inputs
        of the study, thereby caching any data required from remote
        repositorys. Useful when launching many parallel jobs that will
        all try to concurrently access the remote repository, and probably
        lead to timeout errors.
        """
        workflow = pe.Workflow(name='cache_download',
                               base_dir=self.processor.work_dir)
        subjects = pe.Node(IdentityInterface(['subject_id']), name='subjects',
                           environment=self.environment)
        sessions = pe.Node(IdentityInterface(['subject_id', 'visit_id']),
                           name='sessions', environment=self.environment)
        subjects.iterables = ('subject_id', tuple(self.subject_ids))
        sessions.iterables = ('visit_id', tuple(self.visit_ids))
        source = pe.Node(RepositorySource(
            self.bound_spec(i).collection for i in self.inputs), name='source')
        workflow.connect(subjects, 'subject_id', sessions, 'subject_id')
        workflow.connect(sessions, 'subject_id', source, 'subject_id')
        workflow.connect(sessions, 'visit_id', source, 'visit_id')
        workflow.run()

    @classmethod
    def print_specs(cls):
        print('Available data:')
        for spec in cls.data_specs():
            print(spec)
        print('\nAvailable parameters:')
        for spec in cls.parameter_specs():
            print(spec)

    def provided(self, spec_name, default_okay=True):
        """
        Checks to see whether the corresponding data spec was provided an
        explicit input, as opposed to derivatives or missing optional inputs

        Parameters
        ----------
        spec_name : str
            Name of a data spec
        """
        spec = self.bound_spec(spec_name)
        if not spec.derived:
            return spec.default is None and default_okay
        else:
            return True

    @classmethod
    def freq_from_iterfields(cls, iterfields):
        """
        Returns the frequency corresponding to the given iterfields
        """
        return {
            set(it): f for f, it in cls.FREQUENCIES.items()}[set(iterfields)]


class StudyMetaClass(type):
    """
    Metaclass for all study classes that collates data specs from
    bases and checks pipeline method names.

    Combines specifications in add_(data|parameter)_specs from
    the class to be created with its base classes, overriding matching
    specs in the order of the bases.
    """

    def __new__(metacls, name, bases, dct):  # @NoSelf @UnusedVariable
        if not any(issubclass(b, Study) for b in bases):
            raise ArcanaUsageError(
                "StudyMetaClass can only be used for classes that "
                "have Study as a base class")
        try:
            add_data_specs = dct['add_data_specs']
        except KeyError:
            add_data_specs = []
        try:
            add_param_specs = dct['add_param_specs']
        except KeyError:
            add_param_specs = []
        combined_attrs = set()
        combined_data_specs = {}
        combined_param_specs = {}
        for base in reversed(bases):
            # Get the combined class dictionary including base dicts
            # excluding auto-added properties for data and parameter specs
            combined_attrs.update(
                a for a in dir(base) if (not issubclass(base, Study) or
                                         a not in base.spec_names()))
            try:
                combined_data_specs.update(
                    (d.name, d) for d in base.data_specs())
            except AttributeError:
                pass
            try:
                combined_param_specs.update(
                    (p.name, p) for p in base.parameter_specs())
            except AttributeError:
                pass
        combined_attrs.update(list(dct.keys()))
        combined_data_specs.update((d.name, d) for d in add_data_specs)
        combined_param_specs.update(
            (p.name, p) for p in add_param_specs)
        # Check that the pipeline names in data specs correspond to a
        # pipeline method in the class
        for spec in add_data_specs:
            if spec.derived:
                if spec.pipeline_name == 'pipeline':
                    raise ArcanaDesignError(
                        "Cannot use the name 'pipeline' for the name of a "
                        "pipeline constructor in class {} as it clashes "
                        "with base method to create pipelines"
                        .format(name))
                if spec.pipeline_name not in combined_attrs:
                    raise ArcanaDesignError(
                        "Pipeline to generate '{}', '{}', is not present"
                        " in '{}' class".format(
                            spec.name, spec.pipeline_name, name))
        # Check for name clashes between data and parameter specs
        spec_name_clashes = (set(combined_data_specs) &
                             set(combined_param_specs))
        if spec_name_clashes:
            raise ArcanaDesignError(
                "'{}' name both data and parameter specs in '{}' class"
                .format("', '".join(spec_name_clashes), name))
        reserved_clashes = [n for n in combined_data_specs
                            if n in Study.ITERFIELDS]
        if reserved_clashes:
            raise ArcanaDesignError(
                "'{}' data spec names clash with reserved names"
                .format("', '".join(reserved_clashes), name))
        dct['_data_specs'] = combined_data_specs
        dct['_param_specs'] = combined_param_specs
        if '__metaclass__' not in dct:
            dct['__metaclass__'] = metacls
        return type(name, bases, dct)


def pickle_reconstructor(metacls, name, bases, cls_dict):
    obj = DummyObject()
    obj.__class__ = metacls(name, bases, cls_dict)
    return obj


class DummyObject(object):
    pass
