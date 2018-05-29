import tempfile
import shutil
import os.path
import unittest
from unittest import TestCase
from nipype.interfaces.utility import IdentityInterface
from arcana.testing import BaseTestCase, BaseMultiSubjectTestCase
from arcana.study.base import Study, StudyMetaClass
from arcana.option import OptionSpec
from arcana.dataset import DatasetSpec, FieldSpec, DatasetMatch
from arcana.data_format import text_format, DataFormat
from future.utils import PY2
if PY2:
    import cPickle as pkl  # @UnusedImport
else:
    import pickle as pkl  # @Reimport

# For testing DICOM tag matching
dicom_format = DataFormat(name='dicom', extension=None,
                          directory=True, within_dir_exts=['.dcm'])
DataFormat.register(dicom_format)


class TestDatasetSpecPickle(TestCase):

    datasets = []
    fields = []

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.pkl_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)
        shutil.rmtree(self.pkl_dir)

    def test_dataset_and_field(self):
        objs = [DatasetSpec('a', text_format,
                            'dummy_pipeline1'),
                FieldSpec('b', int, 'dummy_pipeline2')]
        for i, obj in enumerate(objs):
            fname = os.path.join(self.pkl_dir, '{}.pkl'.format(i))
            with open(fname, 'w') as f:
                pkl.dump(obj, f)
            with open(fname) as f:
                re_obj = pkl.load(f)
            self.assertEqual(obj, re_obj)


class TestMatchStudy(Study):

    __metaclass__ = StudyMetaClass

    add_data_specs = [
        DatasetSpec('gre_phase', dicom_format),
        DatasetSpec('gre_mag', dicom_format)]

    def dummy_pipeline1(self):
        pass

    def dummy_pipeline2(self):
        pass


class TestDatasetMatching(BaseMultiSubjectTestCase):

    @unittest.skip("Test not implemented")
    def test_match_pattern(self):
        pass


class TestDicomTagMatch(BaseTestCase):

    IMAGE_TYPE_TAG = ('0008', '0008')
    GRE_PATTERN = 'gre_field_mapping_3mm.*'
    PHASE_IMAGE_TYPE = ['ORIGINAL', 'PRIMARY', 'P', 'ND']
    MAG_IMAGE_TYPE = ['ORIGINAL', 'PRIMARY', 'M', 'ND', 'NORM']
    DICOM_MATCH = [
        DatasetMatch('gre_phase', dicom_format, GRE_PATTERN,
                     dicom_tags={IMAGE_TYPE_TAG: PHASE_IMAGE_TYPE},
                     is_regex=True),
        DatasetMatch('gre_mag', dicom_format, GRE_PATTERN,
                     dicom_tags={IMAGE_TYPE_TAG: MAG_IMAGE_TYPE},
                     is_regex=True)]

    INPUTS_FROM_REF_DIR = True

    def test_dicom_match(self):
        study = self.create_study(
            TestMatchStudy, 'test_dicom',
            inputs=self.DICOM_MATCH)
        phase = study.data('gre_phase')[0]
        mag = study.data('gre_mag')[0]
        self.assertEqual(phase.name, 'gre_field_mapping_3mm_phase')
        self.assertEqual(mag.name, 'gre_field_mapping_3mm_mag')

    def test_order_match(self):
        study = self.create_study(
            TestMatchStudy, 'test_dicom',
            inputs=[
                DatasetMatch('gre_phase', dicom_format,
                             pattern=self.GRE_PATTERN, order=1,
                             is_regex=True),
                DatasetMatch('gre_mag', dicom_format,
                             pattern=self.GRE_PATTERN, order=0,
                             is_regex=True)])
        phase = study.data('gre_phase')[0]
        mag = study.data('gre_mag')[0]
        self.assertEqual(phase.name, 'gre_field_mapping_3mm_phase')
        self.assertEqual(mag.name, 'gre_field_mapping_3mm_mag')


class TestDerivableStudy(Study):

    __metaclass__ = StudyMetaClass

    add_data_specs = [
        DatasetSpec('required', text_format),
        DatasetSpec('optional', text_format, optional=True),
        DatasetSpec('derivable', text_format, 'pipeline1'),
        DatasetSpec('missing_input', text_format, 'pipeline2'),
        DatasetSpec('another_derivable', text_format, 'pipeline3'),
        DatasetSpec('wrong_option', text_format, 'pipeline3'),
        DatasetSpec('wrong_option2', text_format, 'pipeline4')]

    add_option_specs = [
        OptionSpec('switch', 0)]

    def pipeline1(self):
        pipeline = self.create_pipeline(
            'pipeline1',
            inputs=[DatasetSpec('required', text_format)],
            outputs=[DatasetSpec('derivable', text_format)],
            desc="",
            citations=[],
            version=1)
        identity = pipeline.create_node(IdentityInterface(['a']),
                                        'identity')
        pipeline.connect_input('required', identity, 'a')
        pipeline.connect_output('derivable', identity, 'a')
        return pipeline

    def pipeline2(self):
        pipeline = self.create_pipeline(
            'pipeline2',
            inputs=[DatasetSpec('required', text_format),
                    DatasetSpec('optional', text_format)],
            outputs=[DatasetSpec('missing_input', text_format)],
            desc="",
            citations=[],
            version=1)
        identity = pipeline.create_node(IdentityInterface(['a', 'b']),
                                        'identity')
        pipeline.connect_input('required', identity, 'a')
        pipeline.connect_input('optional', identity, 'b')
        pipeline.connect_output('missing_input', identity, 'a')
        return pipeline

    def pipeline3(self, **kwargs):
        outputs = [DatasetSpec('another_derivable', text_format)]
        switch = self.pre_option('switch', 'pipeline3', **kwargs)
        if switch:
            outputs.append(DatasetSpec('wrong_option', text_format))
        pipeline = self.create_pipeline(
            'pipeline3',
            inputs=[DatasetSpec('required', text_format)],
            outputs=outputs,
            desc="",
            citations=[],
            version=1)
        identity = pipeline.create_node(IdentityInterface(['a', 'b']),
                                        'identity')
        pipeline.connect_input('required', identity, 'a')
        pipeline.connect_input('required', identity, 'b')
        pipeline.connect_output('another_derivable', identity, 'a')
        if switch:
            pipeline.connect_output('wrong_option', identity, 'b')
        return pipeline

    def pipeline4(self, **kwargs):
        pipeline = self.create_pipeline(
            'pipeline4',
            inputs=[DatasetSpec('wrong_option', text_format)],
            outputs=[DatasetSpec('wrong_option2', text_format)],
            desc="",
            citations=[],
            version=1, **kwargs)
        identity = pipeline.create_node(IdentityInterface(['a']),
                                        'identity')
        pipeline.connect_input('wrong_option', identity, 'a')
        pipeline.connect_output('wrong_option2', identity, 'a')
        return pipeline


class TestDerivable(BaseTestCase):

    INPUT_DATASETS = {'required': 'blah'}

    def test_derivable(self):
        # Test vanilla study
        study = self.create_study(
            TestDerivableStudy,
            'study',
            inputs=[DatasetMatch('required', text_format, 'required')])
        self.assertTrue(study.spec('derivable').derivable)
        self.assertTrue(
            study.spec('another_derivable').derivable)
        self.assertFalse(
            study.spec('missing_input').derivable)
        self.assertFalse(
            study.spec('wrong_option').derivable)
        self.assertFalse(
            study.spec('wrong_option2').derivable)
        # Test study with 'switch' enabled
        study_with_switch = self.create_study(
            TestDerivableStudy,
            'study_with_switch',
            inputs=[DatasetMatch('required', text_format, 'required')],
            options={'switch': 1})
        self.assertTrue(
            study_with_switch.spec('wrong_option').derivable)
        self.assertTrue(
            study_with_switch.spec('wrong_option2').derivable)
        # Test study with optional input
        study_with_input = self.create_study(
            TestDerivableStudy,
            'study_with_inputs',
            inputs=[DatasetMatch('required', text_format, 'required'),
                    DatasetMatch('optional', text_format, 'required')])
        self.assertTrue(
            study_with_input.spec('missing_input').derivable)
