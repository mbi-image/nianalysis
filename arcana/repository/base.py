from builtins import object
from abc import ABCMeta, abstractmethod
from arcana.node import Node
from future.utils import with_metaclass
from arcana.interfaces.repository import (RepositorySource,
                                          RepositorySink)
import logging

logger = logging.getLogger('arcana')


class BaseRepository(with_metaclass(ABCMeta, object)):
    """
    Abstract base class for all Repository systems, DaRIS, XNAT and
    local file system. Sets out the interface that all Repository
    classes should implement.
    """

    def __init__(self):
        self._connection_depth = 0

    def __enter__(self):
        # This allows the repository to be used within nested contexts
        # but still only use one connection. This is useful for calling
        # methods that need connections, and therefore control their
        # own connection, in batches using the same connection by
        # placing the batch calls within an outer context.
        if self._connection_depth == 0:
            self.connect()
        self._connection_depth += 1
        return self

    def __exit__(self, exception_type, exception_value, traceback):  # @UnusedVariable @IgnorePep8
        self._connection_depth -= 1
        if self._connection_depth == 0:
            self.disconnect()

    def connect(self):
        """
        If a connection session is required to the repository,
        manage it here
        """

    def disconnect(self):
        """
        If a connection session is required to the repository,
        manage it here
        """

    @abstractmethod
    def get_dataset(self, dataset):
        """
        If the repository is remote, cache the dataset here
        """
        pass

    @abstractmethod
    def get_field(self, field):
        """
        If the repository is remote, cache the dataset here
        """
        pass

    @abstractmethod
    def put_dataset(self, dataset):
        """
        Inserts or updates the dataset into the repository
        """

    @abstractmethod
    def put_field(self, field):
        """
        Inserts or updates the fields into the repository
        """

    @abstractmethod
    def tree(self, subject_ids=None, visit_ids=None):
        """
        Return the tree of subject and sessions information within a
        project in the XNAT repository

        Parameters
        ----------
        subject_ids : list(str)
            List of subject IDs with which to filter the tree with. If
            None all are returned
        visit_ids : list(str)
            List of visit IDs with which to filter the tree with. If
            None all are returned

        Returns
        -------
        project : arcana.repository.Project
            A hierarchical tree of subject, session and dataset
            information for the repository
        """

    def source(self, inputs, name=None):
        """
        Returns a NiPype node that gets the input data from the repository
        system. The input spec of the node's interface should inherit from
        RepositorySourceInputSpec

        Parameters
        ----------
        project_id : str
            The ID of the project to return the sessions for
        inputs : list(Dataset|Field)
            An iterable of arcana.Dataset or arcana.Field
            objects, which specify the datasets to extract from the
            repository system
        name : str
            Name of the NiPype node
        study_name: str
            Prefix used to distinguish datasets generated by a particular
            study. Used for derived datasets only
        """
        if name is None:
            name = "{}_source".format(self.type)
        return Node(RepositorySource(
            i.collection for i in inputs), name=name)

    def sink(self, outputs, frequency='per_session', name=None):
        """
        Returns a NiPype node that puts the output data back to the repository
        system. The input spec of the node's interface should inherit from
        RepositorySinkInputSpec

        Parameters
        ----------
        project_id : str
            The ID of the project to return the sessions for
        outputs : List(BaseFile|Field) | list(
            An iterable of arcana.Dataset arcana.Field objects,
            which specify the datasets to put into the repository system
        name : str
            Name of the NiPype node
        study_name: str
            Prefix used to distinguish datasets generated by a particular
            study. Used for derived datasets only

        """
        if name is None:
            name = "{}_{}_sink".format(self.type, frequency)
        return Node(RepositorySink((o.collection for o in outputs),
                                   frequency), name=name)

    def __ne__(self, other):
        return not (self == other)
