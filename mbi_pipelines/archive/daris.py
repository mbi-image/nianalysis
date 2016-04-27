import os.path
import shutil
import subprocess
import stat
import logging
from lxml import etree
from nipype.interfaces.base import (
    Directory, DynamicTraitedSpec, traits, TraitedSpec, BaseInterfaceInputSpec,
    isdefined, Undefined)
from nipype.pipeline import engine as pe
from nipype.interfaces.io import IOBase, DataSink, add_traits
from mbi_pipelines.exception import (
    DarisException, DarisNameNotFoundException)
from collections import namedtuple
from .base import Archive

DarisEntry = namedtuple('DarisEntry', 'id name description ctime mtime')

logger = logging.getLogger('MBIPipelines')


class Daris(Archive):

    def __init__(self, user, password, cache_dir,
                 server='mf-erc.its.monash.edu.au', domain='monash-ldap',
                 repo_id=2):
        self._server = server
        self._domain = domain
        self._user = user
        self._password = password
        self._cache_dir = cache_dir
        self._repo_id = repo_id

    def source(self, project_id):
        source = pe.Node(DarisSource(), name="daris_source")
        source.inputs.server = self._server
        source.inputs.domain = self._domain
        source.inputs.user = self._user
        source.inputs.password = self._password
        source.inputs.cache_dir = self._cache_dir
        source.inputs.project_id = project_id
        source.inputs.repo_id = self._repo_id
        return source

    def sink(self, project_id):
        sink = pe.Node(DarisSource(), name="daris_sink")
        sink.inputs.server = self._server
        sink.inputs.domain = self._domain
        sink.inputs.user = self._user
        sink.inputs.password = self._password
        sink.inputs.cache_dir = self._cache_dir
        sink.inputs.project_id = project_id
        sink.inputs.repo_id = self._repo_id
        return sink

    def subject_ids(self, project_id, repo_id=2):
        with DarisSession(server=self._server, domain=self._domain,
                          user=self._user, password=self._password) as daris:
            entries = daris.get_subjects(self, project_id, repo_id=repo_id)
        return [e.id for e in entries]

    def sessions_with_file(self, project_id, sessions):
        raise NotImplementedError


class DarisSourceInputSpec(TraitedSpec):
    project_id = traits.Int(mandatory=True, desc='The project ID')  # @UndefinedVariable @IgnorePep8
    subject_id = traits.Int(mandatory=True, desc="The subject ID")  # @UndefinedVariable @IgnorePep8
    study_id = traits.Int(1, mandatory=True, usedefult=True,  # @UndefinedVariable @IgnorePep8
                            desc="The time point or processed data process ID")
    repo_id = traits.Int(2, mandatory=True, usedefault=True, # @UndefinedVariable @IgnorePep8
                         desc='The ID of the repository')
    files = traits.List(  # @UndefinedVariable
        traits.Tuple(  # @UndefinedVariable
            traits.Str(mandatory=True, desc="name of file"),  # @UndefinedVariable @IgnorePep8
            traits.Bool(mandatory=True,  # @UndefinedVariable @IgnorePep8
                        desc="whether the file is processed or not")),
        desc="Names of all files that comprise the complete file")
    cache_dir = Directory(
        exists=True, desc=("Path to the base directory where the downloaded"
                           "files will be cached"))
    server = traits.Str('mf-erc.its.monash.edu.au', mandatory=True,  # @UndefinedVariable @IgnorePep8
                        usedefault=True, desc="The address of the MF server")
    domain = traits.Str('monash-ldap', mandatory=True, usedefault=True,  # @UndefinedVariable @IgnorePep8
                        desc="The domain of the username/password")
    user = traits.Str(None, mandatory=True, usedefault=True,  # @UndefinedVariable @IgnorePep8
                      desc="The DaRIS username to log in with")
    password = traits.Password(None, mandatory=True, usedefault=True,  # @UndefinedVariable @IgnorePep8
                               desc="The password of the DaRIS user")


class DarisSource(IOBase):

    input_spec = DarisSourceInputSpec
    output_spec = DynamicTraitedSpec
    _always_run = True

    def __init__(self, infields=None, outfields=None, **kwargs):
        """
        Parameters
        ----------
        infields : list of str
            Indicates the input fields to be dynamically created

        outfields: list of str
            Indicates output fields to be dynamically created

        See class examples for usage

        """
        if not outfields:
            outfields = ['outfiles']
        super(DarisSource, self).__init__(**kwargs)
        undefined_traits = {}
        # used for mandatory inputs check
        self._infields = infields
        self._outfields = outfields
        if infields:
            for key in infields:
                self.inputs.add_trait(key, traits.Any)  # @UndefinedVariable
                undefined_traits[key] = Undefined

    def _list_outputs(self):
        with DarisSession(server=self.inputs.server,
                          domain=self.inputs.domain,
                          user=self.inputs.user,
                          password=self.inputs.password) as daris:
            outputs = {}
            # Create dictionary mapping file names to IDs
            unprocessed_dict = dict((d.name, d) for d in daris.get_files(
                repo_id=self.inputs.repo_id,
                project_id=self.inputs.project_id,
                subject_id=self.inputs.subject_id,
                processed=False,
                study_id=self.inputs.study_id).itervalues())
            processed_dict = dict((d.name, d) for d in daris.get_files(
                repo_id=self.inputs.repo_id,
                project_id=self.inputs.project_id,
                subject_id=self.inputs.subject_id,
                processed=True,
                study_id=self.inputs.study_id).itervalues())
            cache_dirs = {}
            for processed in (False, True):
                cache_dir = os.path.join(*(str(p) for p in (
                    self.inputs.cache_dir, self.inputs.repo_id,
                    self.inputs.project_id, self.inputs.subject_id,
                    (processed + 1), self.inputs.study_id)))
                if not os.path.exists(cache_dir):
                    # Make cache directory with group write permissions
                    os.makedirs(cache_dir, stat.S_IRWXU | stat.S_IRWXG)
                cache_dirs[processed] = cache_dir
            for file_name, processed in self.inputs.files:
                if processed:
                    file_ = processed_dict[file_name]
                else:
                    file_ = unprocessed_dict[file_name]
                cache_path = os.path.join(cache_dirs[processed], file_.name)
                if not os.path.exists(cache_path):
                    daris.download(
                        cache_path, repo_id=self.inputs.repo_id,
                        project_id=self.inputs.project_id,
                        subject_id=self.inputs.subject_id,
                        processed=processed,
                        study_id=self.inputs.study_id,
                        file_id=file_.id)
                outputs[file_name] = cache_path
        return outputs

    def _add_output_traits(self, base):
        """

        Using traits.Any instead out OutputMultiPath till add_trait bug
        is fixed.
        """
        return add_traits(base, [name for name, _ in self.inputs.files])


class DarisSinkInputSpec(DynamicTraitedSpec, BaseInterfaceInputSpec):

    project_id = traits.Int(mandatory=True, desc='The project ID')  # @UndefinedVariable @IgnorePep8
    subject_id = traits.Int(mandatory=True, desc="The subject ID")  # @UndefinedVariable @IgnorePep8
    study_id = traits.Int(mandatory=False, # @UndefinedVariable @IgnorePep8
                          desc="The time point or processed data process ID")
    name = traits.Str(  # @UndefinedVariable @IgnorePep8
        mandatory=True, desc=("The name of the processed data group, e.g. "
                              "'tractography'"))
    description = traits.Str(mandatory=True,  # @UndefinedVariable
                                   desc="Description of the study")
    repo_id = traits.Int(2, mandatory=True, usedefault=True, # @UndefinedVariable @IgnorePep8
                         desc='The ID of the repository')
    cache_dir = Directory(
        exists=True, desc=("Path to the base directory where the files will"
                           " be cached before uploading"))
    file_format = traits.Str('nifti', mandatory=True, usedefault=True,  # @UndefinedVariable @IgnorePep8
                             desc="The file format of the files to sink")
    server = traits.Str('mf-erc.its.monash.edu.au', mandatory=True,  # @UndefinedVariable @IgnorePep8
                        usedefault=True, desc="The address of the MF server")
    domain = traits.Str('monash-ldap', mandatory=True, usedefault=True,  # @UndefinedVariable @IgnorePep8
                        desc="The domain of the username/password")
    user = traits.Str(None, mandatory=True, usedefault=True,  # @UndefinedVariable @IgnorePep8
                      desc="The DaRIS username to log in with")
    password = traits.Password(None, mandatory=True, usedefault=True,  # @UndefinedVariable @IgnorePep8
                               desc="The password of the DaRIS user")
    _outputs = traits.Dict(traits.Str, value={}, usedefault=True)  # @UndefinedVariable @IgnorePep8
    # TODO: Not implemented yet
    overwrite = traits.Bool(  # @UndefinedVariable
        False, mandatory=True, usedefault=True,
        desc=("Whether or not to overwrite previously created studies of the "
              "same name"))

    # Copied from the S3DataSink in the nipype.interfaces.io module
    def __setattr__(self, key, value):
        if key not in self.copyable_trait_names():
            if not isdefined(value):
                super(DarisSinkInputSpec, self).__setattr__(key, value)
            self._outputs[key] = value
        else:
            if key in self._outputs:
                self._outputs[key] = value
            super(DarisSinkInputSpec, self).__setattr__(key, value)


class DarisSinkOutputSpec(TraitedSpec):

    out_file = traits.Any(desc='datasink output')  # @UndefinedVariable
    study_id = traits.Int(desc="The study id, which was potentially created")  # @UndefinedVariable @IgnorePep8


class DarisSink(DataSink):

    input_spec = DarisSinkInputSpec
    output_spec = DarisSinkOutputSpec

    def _list_outputs(self):
        """Execute this module.
        """
        # Initiate outputs
        outputs = self.output_spec().get()
        out_files = []
        missing_files = []
        # Open DaRIS session
        with DarisSession(server=self.inputs.server,
                          domain=self.inputs.domain,
                          user=self.inputs.user,
                          password=self.inputs.password) as daris:
            if self.inputs.study_id is Undefined:
                study_id = None
                add_study = True
            else:
                study_id = self.inputs.study_id
                add_study = not daris.exists(project_id=self.inputs.project_id,
                                             subject_id=self.inputs.subject_id,
                                             study_id=study_id,
                                             repo_id=self.inputs.repo_id)
            outputs['study_id'] = study_id
            if add_study:
                # Add study to hold output
                study_id = daris.add_study(
                    project_id=self.inputs.project_id,
                    subject_id=self.inputs.subject_id,
                    study_id=study_id,
                    repo_id=self.inputs.repo_id,
                    processed=True, name=self.inputs.name,
                    description=self.inputs.description)
            # Get cache dir for study
            out_dir = os.path.abspath(os.path.join(*(str(d) for d in (
                self.inputs.cache_dir, self.inputs.repo_id,
                self.inputs.project_id, self.inputs.subject_id, 2, study_id))))
            # Make study cache dir
            if not os.path.exists(out_dir):
                os.makedirs(out_dir, stat.S_IRWXU | stat.S_IRWXG)
            # Loop through files connected to the sink and copy them to the
            # cache directory and upload to daris.
            for name, filename in self.inputs._outputs.iteritems():
                src_path = os.path.abspath(filename)
                if not isdefined(src_path):
                    missing_files.append((name, src_path))
                    continue  # skip the upload for this file
                # Copy to local cache
                dst_path = os.path.join(out_dir, name)
                out_files.append(dst_path)
                shutil.copyfile(src_path, dst_path)
                # Upload to DaRIS
                file_id = daris.add_file(
                    project_id=self.inputs.project_id,
                    subject_id=self.inputs.subject_id,
                    repo_id=self.inputs.repo_id, processed=True,
                    study_id=study_id, name=name,
                    description="Uploaded from DarisSink")
                daris.upload(
                    src_path, project_id=self.inputs.project_id,
                    subject_id=self.inputs.subject_id,
                    repo_id=self.inputs.repo_id, processed=True,
                    study_id=study_id, file_id=file_id,
                    file_format=self.inputs.file_format)
        if missing_files:
            # FIXME: Not sure if this should be an exception or not,
            #        indicates a problem but stopping now would throw
            #        away the files that were created
            logger.warning(
                "Missing output files '{}' mapped to names '{}' in "
                "DarisSink".format("', '".join(f for _, f in missing_files),
                                   "', '".join(n for n, _ in missing_files)))
        # Return cache file paths
        outputs['out_file'] = out_files
        return outputs


class DarisSession:
    """
    Handles the connection to the MediaFlux server, logs into the DaRIS
    application and runs MediaFlux commands
    """
    _namespaces = {'daris': 'daris'}
    DEFAULT_REPO = 2
    _entry_xpaths = ('cid', 'meta/daris:pssd-object/name',
                     'meta/daris:pssd-object/description', 'ctime', 'mtime')

    def __init__(self, server='mf-erc.its.monash.edu.au', domain='monash-ldap',
                 user=None, password=None, token_path=None,
                 app_name='python_daris'):
        """
        server     -- the host name or IP of the daris server
        domain     -- the login domain of the user to login with
        user       -- the username of the user to login with
        password   -- the password for the user
        token_path -- path to the token file to use for authentication. If it
                      doesn't exist it will be created using the username and
                      password provided
        """
        if user is None:
            user = os.environ.get('DARIS_USER', None)
        if password is None:
            password = os.environ.get('DARIS_PASSWORD', None)
        if ((token_path is None or not os.path.exists(token_path)) and
                None in (user, password)):
            raise DarisException(
                "Username and password must be provided if no token is "
                "given and the environment variables 'DARIS_USER' and "
                "'DARIS_PASSWORD' are not set")
        self._server = server
        self._domain = domain
        self._user = user
        self._password = password
        self._token_path = token_path
        self._app_name = app_name
        self._mfsid = None
        if token_path is not None and os.path.exists(token_path):
            with open(token_path) as f:
                self._token = f.readline()
        else:
            self._token = None

    def open(self):
        """
        Opens the session. Should usually be used within a 'with' context, e.g.

            with DarisSession() as session:
                session.run("my-cmd")

        to ensure that the session is always closed afterwards
        """
        if self._token is not None:
            # Get MediaFlux SID from token logon
            self._mfsid = self.run("system.logon :app {} :token {}"
                                    .format(self._app_name, self._token),
                                    logon=True)
        else:
            # Logon to DaRIS using user name
            self._mfsid = self.run("logon {} {} {}".format(
                self._domain, self._user, self._password), logon=True)
            if self._token_path is not None:
                # Generate token if it doesn't already exist
                self._token = self.run(
                    "secure.identity.token.create :app {}"
                    .format(self._app_name), logon=True)
                # ":destroy-on-service-call system.logoff"
                with open(self._token_path, 'w') as f:
                    f.write(self._token)
                # Change permissions to owner read only
                os.chmod(self._token_path, stat.S_IRUSR)

    def close(self):
        if self._mfsid:
            self.run('logoff')
            self._mfsid = None

    def __enter__(self):
        """
        This allows the daris session to be used in 'with' statements, e.g.

            with DarisSession() as daris:
                daris.print_entries(daris.list_projects())

        and ensure that the session is closed again after the code runs
        (including on errors)
        """
        self.open()
        return self

    def __exit__(self, type_, value, traceback):  # @UnusedVariable
        self.close()

    def download(self, location, project_id, subject_id, file_id,
                 study_id=1, processed=False, repo_id=2):
        # Construct CID
        cid = "1008.{}.{}.{}.{}.{}.{}".format(
            repo_id, project_id, subject_id, (processed + 1), study_id,
            file_id)
        self.run("asset.get :cid {} :out file:{}".format(cid, location))

    def upload(self, location, project_id, subject_id, study_id, file_id,
               name=None, repo_id=2, processed=True, file_format='nifti'):
        # Use the name of the file to be uploaded if the 'name' kwarg is
        # present
        if name is None:
            name = os.path.basename(location)
        # Determine whether file is NifTI depending on file extension
        # FIXME: Need a better way to determine the filetype
        if file_format is 'nifti' or location.endswith('.nii.gz'):
            file_format = " :lctype nifti/series "
        else:
            file_format = ""
        cmd = (
            "om.pssd.dataset.derivation.update :id 1008.{}.{}.{}.{}.{}.{} "
            " :in file:{} :filename \"{}\"{}".format(
                repo_id, project_id, subject_id, (processed + 1), study_id,
                file_id, location, name, file_format))
        self.run(cmd)

    def get_projects(self, repo_id=2):
        """
        Lists all projects in the repository

        repo_id     -- the ID of the DaRIS repo (Monash is 2)
        """
        return self.query(
            "cid starts with '1008.{}' and model='om.pssd.project'"
            .format(repo_id))

    def get_subjects(self, project_id, repo_id=2):
        """
        Lists all projects in a project

        project_id  -- the ID of the project to list the subjects for
        repo_id     -- the ID of the DaRIS repo (Monash is 2)
        """
        return self.query(
            "cid starts with '1008.{}.{}' and model='om.pssd.subject'"
            .format(repo_id, project_id))

    def get_studies(self, project_id, subject_id, repo_id=2, processed=False):
        return self.query(
            "cid starts with '1008.{}.{}.{}.{}' and model='om.pssd.study'"
            .format(repo_id, project_id, subject_id, (processed + 1)))

    def get_files(self, project_id, subject_id, study_id=1, repo_id=2,
                     processed=False):
        return self.query(
            "cid starts with '1008.{}.{}.{}.{}.{}' and model='om.pssd.dataset'"
            .format(repo_id, project_id, subject_id, (processed + 1),
                    study_id))

    def print_entries(self, entries):
        for entry in entries.itervalues():
            print '{} {}: {}'.format(entry.id, entry.name, entry.descr)

    def add_subject(self, project_id, subject_id=None, name=None,
                    description='\"\"', repo_id=2):
        """
        Adds a new subject with the given subject_id within the given
        project_id.

        project_id  -- The id of the project to add the subject to
        subject_id  -- The subject_id of the subject to add. If not provided
                       the next available subject_id is used
        name        -- The name of the subject
        description -- A description of the subject
        """
        if subject_id is None:
            # Get the next unused subject id
            try:
                max_subject_id = max(
                    self.get_subjects(project_id, repo_id=repo_id))
            except ValueError:
                max_subject_id = 0  # If there are no subjects
            subject_id = max_subject_id + 1
        if name is None:
            name = 'Subject_{}'.format(subject_id)
        cmd = (
            "om.pssd.subject.create :data-use \"unspecified\" :description "
            "\"{}\" :method \"1008.1.16\" :name \"{}\" :pid 1008.{}.{} "
            ":subject-number {}".format(
                description, name, repo_id, project_id, subject_id))
        # Return the id of the newly created subject
        return int(
            self.run(cmd, '/result/id', expect_single=True).split('.')[-1])

    def add_study(self, project_id, subject_id, study_id=None, name=None,
                  description='\"\"', processed=True, repo_id=2):
        """
        Adds a new subject with the given subject_id within the given
        project_id

        project_id  -- The id of the project to add the study to
        subject_id  -- The id of the subject to add the study to
        study_id    -- The study_id of the study to add. If not provided
                       the next available study_id is used
        name        -- The name of the subject
        description -- A description of the subject
        """
        if study_id is None:
            # Get the next unused study id
            try:
                max_study_id = max(
                    self.get_studies(project_id, subject_id,
                                     processed=processed, repo_id=repo_id))
            except ValueError:
                max_study_id = 0
            study_id = max_study_id + 1
        if name is None:
            name = 'Study_{}'.format(study_id)
        if processed:
            # Check to see whether the processed "ex-method" exists
            # (daris' ex-method is being co-opted to differentiate between raw
            # and processed data)
            sid = '1008.{}.{}.{}'.format(repo_id, project_id, subject_id)
            # Create an "ex-method" to hold the processed data
            if not self.exists(sid + '.2'):
                self.run("om.pssd.ex-method.create :mid 1008.1.19 :sid {}"
                         " :exmethod-number 2".format(sid))
        cmd = (
            "om.pssd.study.create :pid 1008.{}.{}.{}.{} :processed {} "
            ":name \"{}\" :description \"{}\" :step 1 :study-number {}".format(
                repo_id, project_id, subject_id, (processed + 1),
                str(processed).lower(), name, description, study_id))
        # Return the id of the newly created study
        return int(
            self.run(cmd, '/result/id', expect_single=True).split('.')[-1])

    def add_file(self, project_id, subject_id, study_id, file_id=None,
                    name=None, description='\"\"', processed=True, repo_id=2):
        """
        Adds a new file with the given subject_id within the given study id

        project_id  -- The id of the project to add the file to
        subject_id  -- The id of the subject to add the file to
        study_id    -- The id of the study to add the file to
        file_id     -- The file_id of the file to add. If not provided
                       the next available file_id is used
        name        -- The name of the subject
        description -- A description of the subject
        """
        if file_id is None:
            # Get the next unused file id
            try:
                max_file_id = max(
                    self.get_files(project_id, subject_id,
                                   study_id=study_id, processed=processed,
                                   repo_id=repo_id))
            except ValueError:
                max_file_id = 0
            file_id = max_file_id + 1
        if name is None:
            name = 'Dataset_{}'.format(file_id)
        if processed:
            meta = (" :meta \< :mbi.processed.study.properties \< "  # :step 1
                    ":study-reference 1008.{}.{}.{}.1 \> \>".format(
                        repo_id, project_id, subject_id))
        else:
            meta = ""
        cmd = ("om.pssd.dataset.derivation.create :pid 1008.{}.{}.{}.{}.{}"
               " :processed {} :name \"{}\" :description \"{}\"{}".format(
                   repo_id, project_id, subject_id, (processed + 1), study_id,
                   str(processed).lower(), name, description, meta))
        # Return the id of the newly created remote file
        return int(
            self.run(cmd, '/result/id', expect_single=True).split('.')[-1])

    def delete_subject(self, project_id, subject_id, repo_id=2):
        cmd = (
            "om.pssd.object.destroy :cid 1008.{}.{}.{} "
            ":destroy-cid true".format(repo_id, project_id, subject_id))
        self.run(cmd)

    def delete_study(self, project_id, subject_id, study_id, processed=True,
                     repo_id=2):
        cmd = (
            "om.pssd.object.destroy :cid 1008.{}.{}.{}.{}.{} "
            ":destroy-cid true".format(
                repo_id, project_id, subject_id, (processed + 1), study_id))
        self.run(cmd)

    def delete_file(self, project_id, subject_id, study_id, file_id,
                       processed=True, repo_id=2):
        cmd = (
            "om.pssd.object.destroy :cid 1008.{}.{}.{}.{}.{}.{} "
            ":destroy-cid true".format(
                repo_id, project_id, subject_id, (processed + 1), study_id,
                file_id))
        self.run(cmd)

    def find_study(self, name, project_id, subject_id, processed, repo_id=2):
        studies = self.get_studies(
            project_id=self.inputs.project_id,
            subject_id=self.inputs.subject_id,
            repo_id=self.inputs.repo_id, processed=True).itervalues()
        try:
            return next(s for s in studies.itervalues() if s.name == name)
        except StopIteration:
            raise DarisNameNotFoundException(
                "Did not find study named '{}' in 1008.{}.{}.{}.{}"
                .format(repo_id, project_id, subject_id, (processed + 1)))

    def run(self, cmd, xpath=None, expect_single=False, logon=False):
        """
        Executes the aterm.jar and runs the provided aterm command within it

        cmd    -- The aterm command to run
        xpath  -- An xpath filter to the desired element(s)
        single -- Whether the filtered elements should only contain a single
                  result, and if so return its text field instead of the
                  etree.Element
        """
        if not logon and self._mfsid is None:
            raise DarisException(
                "Daris session is closed. DarisSessions are typically used "
                "within 'with' blocks, which ensures they are opened and "
                "closed properly")
        full_cmd = (
            "java -Djava.net.preferIPv4Stack=true -Dmf.host={server} "
            "-Dmf.port=8443 -Dmf.transport=https {mfsid}"
            "-Dmf.result=xml -cp {aterm_path} arc.mf.command.Execute {cmd}"
            .format(server=self._server, cmd=cmd, aterm_path=self.aterm_path(),
                    mfsid=('-Dmf.sid={} '.format(self._mfsid)
                           if not logon else '')))
        try:
            result = subprocess.check_output(
                full_cmd, stderr=subprocess.STDOUT, shell=True).strip()
        except subprocess.CalledProcessError, e:
            raise DarisException(
                "{}: {}".format(e.returncode, e.output.decode()))
        # Extract results from result XML if xpath is provided
        if xpath is not None:
            if isinstance(xpath, basestring):
                result = self._extract_from_xml(result, xpath)
                if expect_single:
                    try:
                        result = result[0].text
                    except IndexError:
                        raise DarisException(
                            "No results found for '{}' xpath".format(xpath))
            else:
                # If 'xpath' is a iterable of xpaths then extract each in turn
                result = [self._extract_from_xml(result, p) for p in xpath]
        return result

    def query(self, query):
        """
        Runs a query command and returns the elements corresponding to the
        provided xpaths
        """
        cmd = ("asset.query :where \"{}\" :action get-meta :size infinity"
               .format(query))
        elements = self.run(cmd, '/result/asset')
        entries = []
        for element in elements:
            args = []
            for xpath in self._entry_xpaths:
                extracted = element.xpath(xpath, namespaces=self._namespaces)
                if len(extracted) == 1:
                    attr = extracted[0].text
                elif not extracted:
                    attr = None
                else:
                    raise DarisException(
                        "Multiple results for given xpath '{}': {}"
                        .format(xpath, "', '".join(e.text for e in extracted)))
                args.append(attr)
            # Strip the ID of the entry from the returned CID (i.e. the
            # number after the last '.'
            entry_id = int(args[0].split('.')[-1])
            entries.append(DarisEntry(entry_id, *args[1:]))
        return dict((e.id, e) for e in entries)

    def exists(self, *args, **kwargs):
        if args:
            assert len(args) == 1
            cid = args[0]
        else:
            try:
                cid = kwargs['cid']
            except KeyError:
                cid = construct_cid(**kwargs)
        result = self.run("asset.exists :cid {}".format(cid), '/result/exists',
                          expect_single=True)
        return result == 'true'

    @classmethod
    def _extract_from_xml(cls, xml_string, xpath):
        doc = etree.XML(xml_string.strip())
        return doc.xpath(xpath, namespaces=cls._namespaces)

    @classmethod
    def aterm_path(cls):
        return os.path.join(os.path.dirname(os.path.realpath(__file__)),
                            '_extern', 'aterm.jar')


def construct_cid(project_id, subject_id=None, study_id=None,
                  processed=None, file_id=None, repo_id=2):
    """
    Returns the CID (unique asset identifier for DaRIS) from the combination of
    sub ids
    """
    cid = '1008.{}.{}'.format(repo_id, project_id)
    ids = (subject_id, study_id, processed, file_id)
    for i, id_ in enumerate(ids):
        if id_ is not None:
            cid += '.{}'.format(int(id_))
        else:
            if any(d is not None for d in ids[(i + 1):]):
                assert False
            else:
                break
    return cid