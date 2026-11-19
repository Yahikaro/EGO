"""UI-free tests of chat continuity; no model, keys, or network required."""
import threading
from unittest.mock import patch
from windows_ui import playground


def test_existing_chat_reused_and_version_fetched():
    calls=[]
    cid='a1234567-1234-4234-8234-123456789012'
    gid='b1234567-1234-4234-8234-123456789012'
    def request(root, method, path, payload=None, timeout=None):
        calls.append((method,path,payload))
        if path=='/v1/system/status': return {'runtime':'ready','active_generation':None}
        if path=='/v1/chats': return [{'id':cid,'version':4,'title':'test'}]
        if path=='/v1/generations' and method=='POST':
            assert payload['chat_id']==cid
            assert payload['expected_version']==4
            return {'generation':{'id':gid}}
        if path==f'/v1/generations/{gid}': return {'state':'completed','text':'Hello'}
        raise AssertionError(path)
    with patch.object(playground.control,'request',side_effect=request):
        result=playground.generate('root','hello',lambda event: None,threading.Event(),chat_id=cid)
    assert result['status']=='PASS' and result['chat_id']==cid
    assert not any(m=='POST' and p=='/v1/chats' for m,p,_ in calls)


def test_new_chat_is_created_once():
    cid='a1234567-1234-4234-8234-123456789012'
    gid='b1234567-1234-4234-8234-123456789012'
    calls=[]
    def request(root, method, path, payload=None, timeout=None):
        calls.append(path)
        if path=='/v1/system/status': return {'runtime':'ready'}
        if path=='/v1/chats': return {'id':cid,'version':1}
        if path=='/v1/generations': return {'generation':{'id':gid}}
        if path==f'/v1/generations/{gid}': return {'state':'completed','text':'ok'}
        raise AssertionError(path)
    with patch.object(playground.control,'request',side_effect=request):
        result=playground.generate('root','hi',lambda event:None,threading.Event())
    assert result['chat_id']==cid
    assert calls.count('/v1/chats')==1
