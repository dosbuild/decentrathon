"""Exact numbered reading packets; no contest content or real approvals."""
import copy
import pytest
from dalel.workspace.approval import snapshot, apply_response, write
from dalel.workspace.service import WorkspaceService
from test_connected import candidate, edited


def test_numbered_packet_rejects_stale_and_requires_explicit_human(tmp_path):
    service=WorkspaceService(tmp_path/'db',simulation=True)
    state=edited(service,service.create(candidate()))
    packet=snapshot(state)
    write(packet,tmp_path/'reading')
    assert packet['rows'][0]['number']==1 and not packet['rows'][0]['reviewed_by_human']
    response=dict(snapshot_sha256=packet['sha256'],approve=[1],reviewer='Synthetic fixture',attest_human=False)
    with pytest.raises(ValueError,match='attestation'):apply_response(service,packet,response)
    response['attest_human']=True
    state=apply_response(service,packet,response)
    assert not service.approval(state,'welcome')
    tampered=copy.deepcopy(packet);tampered['rows'][0]['proposal']='forged'
    with pytest.raises(ValueError,match='hash'):apply_response(service,tampered,response)
    edited(service,state,'Anders {name}','next')
    with pytest.raises(ValueError,match='Stale'):apply_response(service,packet,response)


def test_packet_binds_context_and_has_a_locale(tmp_path):
    from dalel.workspace.approval import context
    from dalel.review.store import digest
    service=WorkspaceService(tmp_path/'db',simulation=True)
    state=edited(service,service.create(candidate()))
    packet=snapshot(state)
    assert packet['target_locale']=='de'
    assert packet['rows'][0]['context_sha256']==digest(context(state,state['entries']['welcome']))
    altered=copy.deepcopy(state)
    altered['entries']['welcome']['description']='Different screen context'
    response=dict(snapshot_sha256=packet['sha256'],approve='all',reviewer='Synthetic fixture',attest_human=True)
    service.get=lambda ident:altered
    with pytest.raises(ValueError,match='context'):apply_response(service,packet,response)
