"""Iteration metadata uses generated implementation evidence, not member names."""
from pathlib import Path
import pytest
from pyv8c.tools.extract_idl import _idl_iteration, _generated_iteration, _apply_iteration, _merge_template, _idl_metadata
from pyv8c.engine.idl import Idl, write_idl, IDL_VERSION

@pytest.mark.parametrize("body,kind", [
    ('iterable<Node>;', 'indexed'), ('iterable<DOMString, long>;', 'pair'),
    ('readonly maplike<DOMString, sequence<long>>;', 'pair'), ('setlike<Node>;', 'set'),
    ('async iterable<Node>;', 'async'), ('void entries(); void keys(); void values(); void forEach();', None),
])
def test_idl_classification(body,kind): assert _idl_iteration(body)==kind


def fixture(evidence,methods=()):
    return {'Fixture': {'sources':['fixture.cc'], 'template':{'_iteration_evidence':evidence},
                        'members':{'prototype':[{'kind':'operation','name':name} for name in methods]}}}

@pytest.mark.parametrize('default,kind', [('entries','pair'),('values','set')])
def test_generated_sync_source_without_idl(default,kind):
    text = 'IsReturnTypeCompatible<SyncIterator<Fixture>>; prototype_object->Get(context, V8AtomicString(isolate, "'+default+'")); prototype_object->DefineOwnProperty(context, v8::Symbol::GetIterator(isolate), v8_value);'
    data=fixture(_generated_iteration(text),['keys','values','entries','forEach'])
    _apply_iteration(data,{})
    assert data['Fixture']['iteration']['kind']==kind
    assert data['Fixture']['iteration']['default']==default


def test_four_operations_do_not_fabricate_iteration():
    data=fixture(_generated_iteration(''),['keys','values','entries','forEach'])
    _apply_iteration(data,{})
    assert 'iteration' not in data['Fixture']


def test_evidence_conflict_and_missing_default_are_errors():
    for evidence in ({'sync':True}, {'sync':True,'defaults':['entries','values']}, {'sync':True,'indexed':True}):
        with pytest.raises(ValueError,match='Fixture'):_apply_iteration(fixture(evidence),{})
    with pytest.raises(ValueError,match='conflict'):
        _apply_iteration(fixture({'sync':True,'defaults':['values']}),{'Fixture':{'iteration':'pair'}})


def test_fragment_merge_keeps_both_evidence_sources():
    a={'_iteration_evidence':{'sync':True,'defaults':[],'methods':[]}}
    b={'_iteration_evidence':{'sync':False,'defaults':['entries'],'methods':[]}}
    data=fixture(_merge_template(a,b)['_iteration_evidence'],['entries'])
    _apply_iteration(data,{})
    assert data['Fixture']['iteration']['kind']=='pair'


def test_partial_idl_preserves_declaration(tmp_path):
    (tmp_path/'a.idl').write_text('interface Fixture { iterable<DOMString, long>; };')
    (tmp_path/'b.idl').write_text('partial interface Fixture { void method(); };')
    assert _idl_metadata(tmp_path)['Fixture']['iteration']=='pair'
    (tmp_path/'b.idl').write_text('partial interface Fixture { setlike<Node>; };')
    with pytest.raises(ValueError,match='Fixture'):_idl_metadata(tmp_path)


def test_async_is_explicitly_unsupported():
    data=fixture(_generated_iteration(''))
    _apply_iteration(data,{'Fixture':{'iteration':'async'}})
    assert 'iteration' not in data['Fixture']
    assert data['Fixture']['async_iteration']=={'supported':False}


def test_legacy_format_is_refused(tmp_path):
    p=write_idl(tmp_path/'old.json',{'idl':{'version':IDL_VERSION-1},'interfaces':{'Fixture':{}}})
    with pytest.raises(ValueError,match='unsupported IDL version'):Idl.load(p,profile=None)


def test_profile_narrows_methods_without_changing_pair_semantics():
    from pyv8c.engine.idl import Profile
    interface={'parent':None,'iteration':{'kind':'pair','default':'entries','methods':['keys','values','entries','forEach'],'iterator':True}}
    profile=Profile(browser='test',exposed=frozenset(),iteration_methods={'Fixture':13})
    table=Idl('test','',{}, {'Fixture':interface}, profile)
    item=table.interface('Fixture')
    assert item.iteration['kind']=='pair'
    assert item.iteration['default']=='entries'
    assert item.iteration_mask==13


def test_unimplemented_sources_are_recorded_and_do_not_retain_bindings():
    pytest.importorskip('pyv8')
    import weakref, gc
    from pyv8c.engine.bindings import Bindings
    from pyv8c.platform.bootstrap import bootstrap_runtime
    document=Idl.load(Path(__file__).resolve().parents[3]/'idls/145.0.7632.162.zip',profile=None)
    for _ in range(2):
        bindings=Bindings(document,runtime=bootstrap_runtime(),stub_logging='off')
        spec=bindings.class_config_for('AudioParamMap')
        assert bindings.unimplemented_iteration_sources==('AudioParamMap',)
        with pytest.raises(NotImplementedError,match='AudioParamMap'):spec.iteration_source(None)
        reference=weakref.ref(bindings)
        del bindings;gc.collect()
        assert reference() is None
