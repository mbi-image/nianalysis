from abc import ABCMeta, abstractmethod
from itertools import chain
from nipype.interfaces.base import (
    DynamicTraitedSpec, traits, TraitedSpec, Undefined, isdefined, File,
    Directory, BaseInterface)
from nianalysis.nodes import Node
from nianalysis.dataset import (
    Dataset, DatasetSpec, FieldSpec, BaseField, BaseDataset)
from nianalysis.exceptions import NiAnalysisError
from nianalysis.utils import PATH_SUFFIX, FIELD_SUFFIX

PATH_TRAIT = traits.Either(File(exists=True), Directory(exists=True))
FIELD_TRAIT = traits.Either(traits.Int, traits.Float, traits.Str)
MULTIPLICITIES = ('per_session', 'per_subject', 'per_visit', 'per_project')


class Archive(object):
    """
    Abstract base class for all Archive systems, DaRIS, XNAT and local file
    system. Sets out the interface that all Archive classes should implement.
    """

    __metaclass__ = ABCMeta

    @abstractmethod
    def source(self, project_id, inputs, name=None, study_name=None):
        """
        Returns a NiPype node that gets the input data from the archive
        system. The input spec of the node's interface should inherit from
        ArchiveSourceInputSpec

        Parameters
        ----------
        project_id : str
            The ID of the project to return the sessions for
        inputs : list(Dataset|Field)
            An iterable of nianalysis.Dataset or nianalysis.Field
            objects, which specify the datasets to extract from the
            archive system
        name : str
            Name of the NiPype node
        study_name: str
            Prefix used to distinguish datasets generated by a particular
            study. Used for processed datasets only
        """
        if name is None:
            name = "{}_source".format(self.type)
        source = Node(self.Source(), name=name)
        source.inputs.project_id = str(project_id)
        source.inputs.datasets = [i.to_tuple() for i in inputs
                                  if isinstance(i, BaseDataset)]
        source.inputs.fields = [i.to_tuple() for i in inputs
                                if isinstance(i, BaseField)]
        if study_name is not None:
            source.inputs.study_name = study_name
        return source

    @abstractmethod
    def sink(self, project_id, outputs, multiplicity='per_session', name=None,
             study_name=None):
        """
        Returns a NiPype node that puts the output data back to the archive
        system. The input spec of the node's interface should inherit from
        ArchiveSinkInputSpec

        Parameters
        ----------
        project_id : str
            The ID of the project to return the sessions for
        outputs : List(BaseFile|Field) | list(
            An iterable of nianalysis.Dataset nianalysis.Field objects,
            which specify the datasets to put into the archive system
        name : str
            Name of the NiPype node
        study_name: str
            Prefix used to distinguish datasets generated by a particular
            study. Used for processed datasets only

        """
        if name is None:
            name = "{}_{}_sink".format(self.type, multiplicity)
        outputs = list(outputs)
        if multiplicity.startswith('per_session'):
            sink_class = self.Sink
        elif multiplicity.startswith('per_subject'):
            sink_class = self.SubjectSink
        elif multiplicity.startswith('per_visit'):
            sink_class = self.VisitSink
        elif multiplicity.startswith('per_project'):
            sink_class = self.ProjectSink
        else:
            raise NiAnalysisError(
                "Unrecognised multiplicity '{}' can be one of '{}'"
                .format(multiplicity,
                        "', '".join(Dataset.MULTIPLICITY_OPTIONS)))
        datasets = [o for o in outputs if isinstance(o, DatasetSpec)]
        fields = [o for o in outputs if isinstance(o, FieldSpec)]
        sink = Node(sink_class(datasets, fields), name=name)
        sink.inputs.project_id = str(project_id)
        sink.inputs.datasets = [s.to_tuple() for s in datasets]
        sink.inputs.fields = [f.to_tuple() for f in fields]
        if study_name is not None:
            sink.inputs.study_name = study_name
        return sink

    @abstractmethod
    def project(self, project_id, subject_ids=None, visit_ids=None):
        """
        Returns a nianalysis.archive.Project object for the given project id,
        which holds information on all available subjects, sessions and
        datasets in the project.

        Parameters
        ----------
        project_id : str
            The ID of the project to return the sessions for
        subject_ids : list(str)
            List of subject ids to filter the returned subjects. If None all
            subjects will be returned.
        visit_ids : list(str)
            List of visit ids to filter the returned sessions. If None all
            sessions will be returned
        """


class BaseArchiveNode(BaseInterface):
    """
    Parameters
    ----------
    infields : list of str
        Indicates the input fields to be dynamically created

    outfields: list of str
        Indicates output fields to be dynamically created

    See class examples for usage

    """

    __metaclass__ = ABCMeta

    def _run_interface(self, runtime, *args, **kwargs):  # @UnusedVariable
        return runtime

    @abstractmethod
    def _list_outputs(self):
        pass

    @classmethod
    def _add_trait(cls, spec, name, trait_type):
        spec.add_trait(name, trait_type)
        spec.trait_set(trait_change_notify=False, **{name: Undefined})
        # Access the trait (not sure why but this is done in add_traits
        # so I have also done it here
        getattr(spec, name)

    def prefix_study_name(self, name, is_spec=True):
        """Prepend study name if defined"""
        if is_spec and isdefined(self.inputs.study_name):
            name = self.inputs.study_name + '_' + name
        return name


class ArchiveSourceInputSpec(DynamicTraitedSpec):
    """
    Base class for archive source input specifications. Provides a common
    interface for 'run_pipeline' when using the archive source to extract
    primary and preprocessed datasets from the archive system
    """
    project_id = traits.Str(  # @UndefinedVariable
        mandatory=True,
        desc='The project ID')
    subject_id = traits.Str(mandatory=True, desc="The subject ID")
    visit_id = traits.Str(mandatory=True, usedefult=True,
                            desc="The visit or processed group ID")
    datasets = traits.List(
        DatasetSpec.traits_spec(),
        desc="List of all datasets to be extracted from the archive")
    fields = traits.List(
        FieldSpec.traits_spec(),
        desc=("List of all the fields that are to be extracted from the"
              "archive"))
    study_name = traits.Str(desc=("Prefix prepended onto processed dataset "
                                  "names"))


class ArchiveSource(BaseArchiveNode):

    output_spec = DynamicTraitedSpec
    _always_run = True

    def _outputs(self):
        return self._add_dataset_and_field_traits(
            super(ArchiveSource, self)._outputs())

    def _add_dataset_and_field_traits(self, base):
        for dataset in self.inputs.datasets:
            self._add_trait(base, dataset[0] + PATH_SUFFIX, PATH_TRAIT)
        # Add output fields
        for name, dtype, _, _, _ in self.inputs.fields:
            self._add_trait(base, name + FIELD_SUFFIX, dtype)
        return base


class BaseArchiveSinkInputSpec(DynamicTraitedSpec):
    """
    Base class for archive sink input specifications. Provides a common
    interface for 'run_pipeline' when using the archive save
    processed datasets in the archive system
    """
    project_id = traits.Str(  # @UndefinedVariable
        mandatory=True,
        desc='The project ID')  # @UndefinedVariable @IgnorePep8

    name = traits.Str(  # @UndefinedVariable @IgnorePep8
        mandatory=True, desc=("The name of the processed data group, e.g. "
                              "'tractography'"))
    description = traits.Str(mandatory=True,  # @UndefinedVariable
                             desc="Description of the study")

    datasets = traits.List(
        DatasetSpec.traits_spec(),
        desc="Lists the datasets to be retrieved from the archive")

    fields = traits.List(
        FieldSpec.traits_spec(),
        desc=("List of all the fields to be retrieved from the archive"))

    # TODO: Not implemented yet
    overwrite = traits.Bool(  # @UndefinedVariable
        False, mandatory=True, usedefault=True,
        desc=("Whether or not to overwrite previously created sessions of the "
              "same name"))
    study_name = traits.Str(desc=("Study name to partition processed datasets "
                                  "by"))

    def __setattr__(self, name, val):
        # Need to check whether datasets is not empty, as it can be when
        # unpickling
        if (isdefined(self.datasets) and self.datasets and
            isdefined(self.fields) and self.fields and
                not hasattr(self, name)):
            accepted = set(chain(
                (s[0] + PATH_SUFFIX for s in self.datasets),
                (f[0] + FIELD_SUFFIX for f in self.fields)))
            if name not in accepted:
                raise NiAnalysisError(
                    "'{}' is not a valid input filename for '{}' archive sink "
                    "(accepts '{}')".format(name, self.name,
                                            "', '".join(accepted)))
        super(BaseArchiveSinkInputSpec, self).__setattr__(name, val)


class ArchiveSinkInputSpec(BaseArchiveSinkInputSpec):

    subject_id = traits.Str(mandatory=True, desc="The subject ID"),
    visit_id = traits.Str(mandatory=False,
                            desc="The session or processed group ID")


class ArchiveSubjectSinkInputSpec(BaseArchiveSinkInputSpec):

    subject_id = traits.Str(mandatory=True, desc="The subject ID")


class ArchiveVisitSinkInputSpec(BaseArchiveSinkInputSpec):

    visit_id = traits.Str(mandatory=True, desc="The visit ID")


class ArchiveProjectSinkInputSpec(BaseArchiveSinkInputSpec):
    pass


class BaseArchiveSinkOutputSpec(TraitedSpec):

    out_files = traits.List(PATH_TRAIT, desc='Output datasets')

    out_fields = traits.List(
        traits.Tuple(traits.Str, FIELD_TRAIT), desc='Output fields')


class ArchiveSinkOutputSpec(BaseArchiveSinkOutputSpec):

    project_id = traits.Str(desc="The project ID")
    subject_id = traits.Str(desc="The subject ID")
    visit_id = traits.Str(desc="The visit ID")


class ArchiveSubjectSinkOutputSpec(BaseArchiveSinkOutputSpec):

    project_id = traits.Str(desc="The project ID")
    subject_id = traits.Str(desc="The subject ID")


class ArchiveVisitSinkOutputSpec(BaseArchiveSinkOutputSpec):

    project_id = traits.Str(desc="The project ID")
    visit_id = traits.Str(desc="The visit ID")


class ArchiveProjectSinkOutputSpec(BaseArchiveSinkOutputSpec):

    project_id = traits.Str(desc="The project ID")


class BaseArchiveSink(BaseArchiveNode):

    def __init__(self, output_datasets, output_fields, **kwargs):
        super(BaseArchiveSink, self).__init__(**kwargs)
        # Add output datasets
        for dataset in output_datasets:
            self._add_trait(self.inputs, dataset.name + PATH_SUFFIX,
                            PATH_TRAIT)
        # Add output fields
        for field in output_fields:
            self._add_trait(self.inputs, field.name + FIELD_SUFFIX,
                            field.dtype)

    @abstractmethod
    def _base_outputs(self):
        "List the base outputs of the sink interface, which relate to the "
        "session/subject/project that is being sunk"


class ArchiveSink(BaseArchiveSink):

    input_spec = ArchiveSinkInputSpec
    output_spec = ArchiveSinkOutputSpec

    multiplicity = 'per_session'

    def _base_outputs(self):
        outputs = self.output_spec().get()
        outputs['project_id'] = self.inputs.project_id
        outputs['subject_id'] = self.inputs.subject_id
        outputs['visit_id'] = self.inputs.visit_id
        return outputs


class ArchiveSubjectSink(BaseArchiveSink):

    input_spec = ArchiveSubjectSinkInputSpec
    output_spec = ArchiveSubjectSinkOutputSpec

    multiplicity = 'per_subject'

    def _base_outputs(self):
        outputs = self.output_spec().get()
        outputs['project_id'] = self.inputs.project_id
        outputs['subject_id'] = self.inputs.subject_id
        return outputs


class ArchiveVisitSink(BaseArchiveSink):

    input_spec = ArchiveVisitSinkInputSpec
    output_spec = ArchiveVisitSinkOutputSpec

    multiplicity = 'per_visit'

    def _base_outputs(self):
        outputs = self.output_spec().get()
        outputs['project_id'] = self.inputs.project_id
        outputs['visit_id'] = self.inputs.visit_id
        return outputs


class ArchiveProjectSink(BaseArchiveSink):

    input_spec = ArchiveProjectSinkInputSpec
    output_spec = ArchiveProjectSinkOutputSpec

    multiplicity = 'per_project'

    def _base_outputs(self):
        outputs = self.output_spec().get()
        outputs['project_id'] = self.inputs.project_id
        return outputs


class Project(object):

    def __init__(self, project_id, subjects, visits, datasets,
                 fields):
        self._id = project_id
        self._subjects = subjects
        self._visits = visits
        self._datasets = datasets
        self._fields = fields

    @property
    def id(self):
        return self._id

    @property
    def subjects(self):
        return iter(self._subjects)

    @property
    def visits(self):
        return iter(self._visits)

    @property
    def datasets(self):
        return self._datasets

    @property
    def fields(self):
        return self._fields

    @property
    def dataset_names(self):
        return (d.name for d in self.datasets)

    @property
    def field_names(self):
        return (f.name for f in self.fields)

    @property
    def data(self):
        return chain(self.datasets, self.fields)

    @property
    def data_names(self):
        return (d.name for d in self.data)

    def __eq__(self, other):
        if not isinstance(other, Project):
            return False
        return (self._id == other._id and
                self._subjects == other._subjects and
                self._visits == other._visits and
                self._datasets == other._datasets and
                self._fields == other._fields)

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):
        return "Subject(id={}, num_subjects={})".format(
            self._id, len(list(self.subjects)))


class Subject(object):
    """
    Holds a subject id and a list of sessions
    """

    def __init__(self, subject_id, sessions, datasets, fields):
        self._id = subject_id
        self._sessions = sessions
        self._datasets = datasets
        self._fields = fields
        for session in sessions:
            session.subject = self

    @property
    def id(self):
        return self._id

    @property
    def sessions(self):
        return iter(self._sessions)

    @property
    def datasets(self):
        return self._datasets

    @property
    def fields(self):
        return self._fields

    @property
    def dataset_names(self):
        return (d.name for d in self.datasets)

    @property
    def field_names(self):
        return (f.name for f in self.fields)

    @property
    def data(self):
        return chain(self.datasets, self.fields)

    @property
    def data_names(self):
        return (d.name for d in self.data)

    def __eq__(self, other):
        if not isinstance(other, Subject):
            return False
        return (self._id == other._id and
                self._sessions == other._sessions and
                self._datasets == other._datasets and
                self._fields == other._fields)

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):
        return "Subject(id={}, num_sessions={})".format(self._id,
                                                        len(self._sessions))


class Visit(object):
    """
    Holds a subject id and a list of sessions
    """

    def __init__(self, visit_id, sessions, datasets, fields):
        self._id = visit_id
        self._sessions = sessions
        self._datasets = datasets
        self._fields = fields
        for session in sessions:
            session.visit = self

    @property
    def id(self):
        return self._id

    @property
    def sessions(self):
        return iter(self._sessions)

    @property
    def datasets(self):
        return self._datasets

    @property
    def fields(self):
        return self._fields

    @property
    def dataset_names(self):
        return (d.name for d in self.datasets)

    @property
    def field_names(self):
        return (f.name for f in self.fields)

    @property
    def data(self):
        return chain(self.datasets, self.fields)

    @property
    def data_names(self):
        return (d.name for d in self.data)

    def __eq__(self, other):
        if not isinstance(other, Subject):
            return False
        return (self._id == other._id and
                self._sessions == other._sessions and
                self._datasets == other._datasets and
                self._fields == other._fields)

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):
        return "Subject(id={}, num_sessions={})".format(self._id,
                                                        len(self._sessions))


class Session(object):
    """
    Holds the session id and the list of datasets loaded from it

    Parameters
    ----------
    subject_id : str
        The subject ID of the session
    visit_id : str
        The visit ID of the session
    datasets : list(Dataset)
        The datasets found in the session
    processed : Session
        If processed scans are stored in a separate session, it is provided
        here
    """

    def __init__(self, subject_id, visit_id, datasets, fields, processed=None):
        self._subject_id = subject_id
        self._visit_id = visit_id
        self._datasets = datasets
        self._fields = fields
        self._subject = None
        self._visit = None
        self._processed = processed

    @property
    def visit_id(self):
        return self._visit_id

    @property
    def subject_id(self):
        return self._subject_id

    @property
    def subject(self):
        return self._subject

    @subject.setter
    def subject(self, subject):
        self._subject = subject

    @property
    def visit(self):
        return self._visit

    @visit.setter
    def visit(self, visit):
        self._visit = visit

    @property
    def processed(self):
        return self._processed

    @processed.setter
    def processed(self, processed):
        self._processed = processed

    @property
    def acquired(self):
        """True if the session contains acquired scans"""
        return not self._processed or self._processed is None

    @property
    def datasets(self):
        return self._datasets

    @property
    def fields(self):
        return self._fields

    @property
    def dataset_names(self):
        return (d.name for d in self.datasets)

    @property
    def field_names(self):
        return (f.name for f in self.fields)

    @property
    def data(self):
        return chain(self.datasets, self.fields)

    @property
    def data_names(self):
        return (d.name for d in self.data)

    @property
    def processed_dataset_names(self):
        datasets = (self.datasets
                    if self.processed is None else self.processed.datasets)
        return (d.name for d in datasets)

    @property
    def processed_field_names(self):
        fields = (self.fields
                    if self.processed is None else self.processed.fields)
        return (f.name for f in fields)

    @property
    def processed_data_names(self):
        return chain(self.processed_dataset_names, self.processed_field_names)

    @property
    def all_dataset_names(self):
        return chain(self.dataset_names, self.processed_dataset_names)

    @property
    def all_field_names(self):
        return chain(self.field_names, self.processed_field_names)

    def __eq__(self, other):
        if not isinstance(other, Session):
            return False
        return (self.subject_id == other.subject_id and
                self.visit_id == other.visit_id and
                self.datasets == other.datasets and
                self.fields == other.fields and
                self.processed == other.processed)

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):
        return ("Session(subject_id='{}', visit_id='{}', num_datasets={}, "
                "num_fields={}, processed={})".format(
                    self.subject_id, self.visit_id, len(self._datasets),
                    len(self._fields), self.processed))
