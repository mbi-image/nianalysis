from arcana.testing import BaseTestCase, TestMath
from arcana.interfaces.utils import Merge
from arcana.dataset import DatasetMatch, DatasetSpec
from arcana.data_format import text_format
from arcana.option import OptionSpec
from arcana.study.base import Study
from arcana.study.multi import (
    MultiStudy, SubStudySpec, MultiStudyMetaClass, StudyMetaClass)
from arcana.option import Option


class StudyA(Study):

    __metaclass__ = StudyMetaClass

    add_data_specs = [
        DatasetSpec('x', text_format),
        DatasetSpec('y', text_format),
        DatasetSpec('z', text_format, 'pipeline_alpha')]

    add_option_specs = [
        OptionSpec('o1', 1),
        OptionSpec('o2', '2'),
        OptionSpec('o3', 3.0)]

    def pipeline_alpha(self, **kwargs):  # @UnusedVariable
        pipeline = self.create_pipeline(
            name='pipeline_alpha',
            inputs=[DatasetSpec('x', text_format),
                    DatasetSpec('y', text_format)],
            outputs=[DatasetSpec('z', text_format)],
            desc="A dummy pipeline used to test MultiStudy class",
            version=1,
            citations=[],
            **kwargs)
        math = pipeline.create_node(TestMath(), name="math")
        math.inputs.op = 'add'
        math.inputs.as_file = True
        # Connect inputs
        pipeline.connect_input('x', math, 'x')
        pipeline.connect_input('y', math, 'y')
        # Connect outputs
        pipeline.connect_output('z', math, 'z')
        return pipeline


class StudyB(Study):

    __metaclass__ = StudyMetaClass

    add_data_specs = [
        DatasetSpec('w', text_format),
        DatasetSpec('x', text_format),
        DatasetSpec('y', text_format, 'pipeline_beta'),
        DatasetSpec('z', text_format, 'pipeline_beta')]

    add_option_specs = [
        OptionSpec('o1', 10),
        OptionSpec('o2', '20'),
        OptionSpec('o3', 30.0),
        OptionSpec('product_op', 'not-specified')]  # Needs to be set to 'product' @IgnorePep8

    def pipeline_beta(self, **kwargs):  # @UnusedVariable
        pipeline = self.create_pipeline(
            name='pipeline_beta',
            inputs=[DatasetSpec('w', text_format),
                    DatasetSpec('x', text_format)],
            outputs=[DatasetSpec('y', text_format),
                     DatasetSpec('z', text_format)],
            desc="A dummy pipeline used to test MultiStudy class",
            version=1,
            citations=[],
            **kwargs)
        add1 = pipeline.create_node(TestMath(), name="add1")
        add2 = pipeline.create_node(TestMath(), name="add2")
        prod = pipeline.create_node(TestMath(), name="product")
        add1.inputs.op = 'add'
        add2.inputs.op = 'add'
        prod.inputs.op = pipeline.option('product_op')
        add1.inputs.as_file = True
        add2.inputs.as_file = True
        prod.inputs.as_file = True
        # Connect inputs
        pipeline.connect_input('w', add1, 'x')
        pipeline.connect_input('x', add1, 'y')
        pipeline.connect_input('x', add2, 'x')
        # Connect nodes
        pipeline.connect(add1, 'z', add2, 'y')
        pipeline.connect(add1, 'z', prod, 'x')
        pipeline.connect(add2, 'z', prod, 'y')
        # Connect outputs
        pipeline.connect_output('y', add2, 'z')
        pipeline.connect_output('z', prod, 'z')
        return pipeline


class FullMultiStudy(MultiStudy):

    __metaclass__ = MultiStudyMetaClass

    add_sub_study_specs = [
        SubStudySpec('ss1', StudyA,
                     {'a': 'x',
                      'b': 'y',
                      'd': 'z',
                      'p1': 'o1',
                      'p2': 'o2',
                      'p3': 'o3'}),
        SubStudySpec('ss2', StudyB,
                     {'b': 'w',
                      'c': 'x',
                      'e': 'y',
                      'f': 'z',
                      'q1': 'o1',
                      'q2': 'o2',
                      'p3': 'o3',
                      'required_op': 'product_op'})]

    add_data_specs = [
        DatasetSpec('a', text_format),
        DatasetSpec('b', text_format),
        DatasetSpec('c', text_format),
        DatasetSpec('d', text_format, 'pipeline_alpha_trans'),
        DatasetSpec('e', text_format, 'pipeline_beta_trans'),
        DatasetSpec('f', text_format, 'pipeline_beta_trans')]

    add_option_specs = [
        OptionSpec('p1', 100),
        OptionSpec('p2', '200'),
        OptionSpec('p3', 300.0),
        OptionSpec('q1', 150),
        OptionSpec('q2', '250'),
        OptionSpec('required_op', 'still-not-specified')]

    pipeline_alpha_trans = MultiStudy.translate(
        'ss1', 'pipeline_alpha')
    pipeline_beta_trans = MultiStudy.translate(
        'ss2', 'pipeline_beta')


class PartialMultiStudy(MultiStudy):

    __metaclass__ = MultiStudyMetaClass

    add_sub_study_specs = [
        SubStudySpec('ss1', StudyA,
                     {'a': 'x', 'b': 'y', 'p1': 'o1'}),
        SubStudySpec('ss2', StudyB,
                     {'b': 'w', 'c': 'x', 'p1': 'o1'})]

    add_data_specs = [
        DatasetSpec('a', text_format),
        DatasetSpec('b', text_format),
        DatasetSpec('c', text_format)]

    pipeline_alpha_trans = MultiStudy.translate(
        'ss1', 'pipeline_alpha')

    add_option_specs = [
        OptionSpec('p1', 1000)]


class MultiMultiStudy(MultiStudy):

    __metaclass__ = MultiStudyMetaClass

    add_sub_study_specs = [
        SubStudySpec('ss1', StudyA, {}),
        SubStudySpec('full', FullMultiStudy),
        SubStudySpec('partial', PartialMultiStudy)]

    add_data_specs = [
        DatasetSpec('g', text_format, 'combined_pipeline')]

    add_option_specs = [
        OptionSpec('combined_op', 'add')]

    def combined_pipeline(self, **kwargs):
        pipeline = self.create_pipeline(
            name='combined',
            inputs=[DatasetSpec('ss1_z', text_format),
                    DatasetSpec('full_e', text_format),
                    DatasetSpec('partial_ss2_z', text_format)],
            outputs=[DatasetSpec('g', text_format)],
            desc=(
                "A dummy pipeline used to test MultiMultiStudy class"),
            version=1,
            citations=[],
            **kwargs)
        merge = pipeline.create_node(Merge(3), name="merge")
        math = pipeline.create_node(TestMath(), name="math")
        math.inputs.op = pipeline.option('combined_op')
        math.inputs.as_file = True
        # Connect inputs
        pipeline.connect_input('ss1_z', merge, 'in1')
        pipeline.connect_input('full_e', merge, 'in2')
        pipeline.connect_input('partial_ss2_z', merge, 'in3')
        # Connect nodes
        pipeline.connect(merge, 'out', math, 'x')
        # Connect outputs
        pipeline.connect_output('g', math, 'z')
        return pipeline


class TestMulti(BaseTestCase):

    INPUT_DATASETS = {'ones': '1'}

    def test_full_multi_study(self):
        study = self.create_study(
            FullMultiStudy, 'full',
            [DatasetMatch('a', text_format, 'ones'),
             DatasetMatch('b', text_format, 'ones'),
             DatasetMatch('c', text_format, 'ones')],
            options=[Option('required_op', 'mul')])
        d = study.data('d', subject_id='SUBJECT', visit_id='VISIT')
        e = study.data('e')[0]
        f = study.data('f')[0]
        self.assertContentsEqual(d, 2.0)
        self.assertContentsEqual(e, 3.0)
        self.assertContentsEqual(f, 6.0)
        # Test option values in MultiStudy
        self.assertEqual(study._get_option('p1').value, 100)
        self.assertEqual(study._get_option('p2').value, '200')
        self.assertEqual(study._get_option('p3').value, 300.0)
        self.assertEqual(study._get_option('q1').value, 150)
        self.assertEqual(study._get_option('q2').value, '250')
        self.assertEqual(study._get_option('required_op').value, 'mul')
        # Test option values in SubStudy
        ss1 = study.sub_study('ss1')
        self.assertEqual(ss1._get_option('o1').value, 100)
        self.assertEqual(ss1._get_option('o2').value, '200')
        self.assertEqual(ss1._get_option('o3').value, 300.0)
        ss2 = study.sub_study('ss2')
        self.assertEqual(ss2._get_option('o1').value, 150)
        self.assertEqual(ss2._get_option('o2').value, '250')
        self.assertEqual(ss2._get_option('o3').value, 300.0)
        self.assertEqual(ss2._get_option('product_op').value, 'mul')

    def test_partial_multi_study(self):
        study = self.create_study(
            PartialMultiStudy, 'partial',
            [DatasetMatch('a', text_format, 'ones'),
             DatasetMatch('b', text_format, 'ones'),
             DatasetMatch('c', text_format, 'ones')],
            options=[Option('ss2_product_op', 'mul')])
        ss1_z = study.data('ss1_z')[0]
        ss2_y = study.data('ss2_y')[0]
        ss2_z = study.data('ss2_z')[0]
        self.assertContentsEqual(ss1_z, 2.0)
        self.assertContentsEqual(ss2_y, 3.0)
        self.assertContentsEqual(ss2_z, 6.0)
        # Test option values in MultiStudy
        self.assertEqual(study._get_option('p1').value, 1000)
        self.assertEqual(study._get_option('ss1_o2').value, '2')
        self.assertEqual(study._get_option('ss1_o3').value, 3.0)
        self.assertEqual(study._get_option('ss2_o2').value, '20')
        self.assertEqual(study._get_option('ss2_o3').value, 30.0)
        self.assertEqual(study._get_option('ss2_product_op').value, 'mul')
        # Test option values in SubStudy
        ss1 = study.sub_study('ss1')
        self.assertEqual(ss1._get_option('o1').value, 1000)
        self.assertEqual(ss1._get_option('o2').value, '2')
        self.assertEqual(ss1._get_option('o3').value, 3.0)
        ss2 = study.sub_study('ss2')
        self.assertEqual(ss2._get_option('o1').value, 1000)
        self.assertEqual(ss2._get_option('o2').value, '20')
        self.assertEqual(ss2._get_option('o3').value, 30.0)
        self.assertEqual(ss2._get_option('product_op').value, 'mul')

    def test_multi_multi_study(self):
        study = self.create_study(
            MultiMultiStudy, 'multi_multi',
            [DatasetMatch('ss1_x', text_format, 'ones'),
             DatasetMatch('ss1_y', text_format, 'ones'),
             DatasetMatch('full_a', text_format, 'ones'),
             DatasetMatch('full_b', text_format, 'ones'),
             DatasetMatch('full_c', text_format, 'ones'),
             DatasetMatch('partial_a', text_format, 'ones'),
             DatasetMatch('partial_b', text_format, 'ones'),
             DatasetMatch('partial_c', text_format, 'ones')],
            options=[Option('full_required_op', 'mul'),
                     Option('partial_ss2_product_op', 'mul')])
        g = study.data('g')[0]
        self.assertContentsEqual(g, 11.0)
        # Test option values in MultiStudy
        self.assertEqual(study._get_option('full_p1').value, 100)
        self.assertEqual(study._get_option('full_p2').value, '200')
        self.assertEqual(study._get_option('full_p3').value, 300.0)
        self.assertEqual(study._get_option('full_q1').value, 150)
        self.assertEqual(study._get_option('full_q2').value, '250')
        self.assertEqual(study._get_option('full_required_op').value,
                         'mul')
        # Test option values in SubStudy
        ss1 = study.sub_study('full').sub_study('ss1')
        self.assertEqual(ss1._get_option('o1').value, 100)
        self.assertEqual(ss1._get_option('o2').value, '200')
        self.assertEqual(ss1._get_option('o3').value, 300.0)
        ss2 = study.sub_study('full').sub_study('ss2')
        self.assertEqual(ss2._get_option('o1').value, 150)
        self.assertEqual(ss2._get_option('o2').value, '250')
        self.assertEqual(ss2._get_option('o3').value, 300.0)
        self.assertEqual(ss2._get_option('product_op').value, 'mul')
        # Test option values in MultiStudy
        self.assertEqual(study._get_option('partial_p1').value, 1000)
        self.assertEqual(study._get_option('partial_ss1_o2').value, '2')
        self.assertEqual(study._get_option('partial_ss1_o3').value, 3.0)
        self.assertEqual(study._get_option('partial_ss2_o2').value, '20')
        self.assertEqual(study._get_option('partial_ss2_o3').value, 30.0)
        self.assertEqual(
            study._get_option('partial_ss2_product_op').value, 'mul')
        # Test option values in SubStudy
        ss1 = study.sub_study('partial').sub_study('ss1')
        self.assertEqual(ss1._get_option('o1').value, 1000)
        self.assertEqual(ss1._get_option('o2').value, '2')
        self.assertEqual(ss1._get_option('o3').value, 3.0)
        ss2 = study.sub_study('partial').sub_study('ss2')
        self.assertEqual(ss2._get_option('o1').value, 1000)
        self.assertEqual(ss2._get_option('o2').value, '20')
        self.assertEqual(ss2._get_option('o3').value, 30.0)
        self.assertEqual(ss2._get_option('product_op').value, 'mul')

    def test_missing_option(self):
        # Misses the required 'full_required_op' option, which sets
        # the operation of the second node in StudyB's pipeline to
        # 'product'
        missing_option_study = self.create_study(
            MultiMultiStudy, 'multi_multi',
            [DatasetMatch('ss1_x', text_format, 'ones'),
             DatasetMatch('ss1_y', text_format, 'ones'),
             DatasetMatch('full_a', text_format, 'ones'),
             DatasetMatch('full_b', text_format, 'ones'),
             DatasetMatch('full_c', text_format, 'ones'),
             DatasetMatch('partial_a', text_format, 'ones'),
             DatasetMatch('partial_b', text_format, 'ones'),
             DatasetMatch('partial_c', text_format, 'ones')],
            options=[Option('partial_ss2_product_op', 'mul')])
        self.assertRaises(
            RuntimeError,
            missing_option_study.data,
            'g')
