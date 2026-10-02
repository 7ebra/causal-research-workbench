import json
from pathlib import Path
import tempfile
import unittest
import export_codex_chat as e


class ExportTests(unittest.TestCase):
    def source(self,records,tail=b''):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        path=Path(directory.name)/'session.jsonl'
        path.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in records)+tail)
        return path

    def msg(self,role,text,phase=None):
        return {'type':'response_item','timestamp':'2026-10-01T00:00:00Z','payload':
            {'type':'message','role':role,'phase':phase,'content':[{'type':'output_text','text':text}]}}

    def test_redaction_and_internal_exclusion(self):
        token='a'*31+'-'+'b'*32
        path=self.source([self.msg('developer','private instructions'),self.msg('assistant','private reasoning','analysis'),
                          self.msg('user','My token is '+token),self.msg('assistant','Visible answer','final_answer')])
        result=e.export(path);encoded=json.dumps(result)
        self.assertNotIn(token,encoded);self.assertNotIn('private instructions',encoded);self.assertNotIn('private reasoning',encoded)
        self.assertEqual(len(result['messages']),2)

    def test_partial_tail_is_reported_not_fabricated(self):
        result=e.export(self.source([self.msg('user','Hello')],b'{"unfinished":'))
        self.assertTrue(result['snapshot']['partial_trailing_record_omitted'])
        self.assertEqual(len(result['messages']),1)

    def test_complete_invalid_record_stops_export(self):
        with self.assertRaises(ValueError):e.export(self.source([self.msg('user','Hello')],b'not json\n'))

    def test_tools_are_optional_and_redacted(self):
        records=[self.msg('user','Hello'),{'type':'response_item','payload':
            {'type':'function_call_output','call_id':'1','output':'Authorization: Bearer '+('x'*32)}}]
        path=self.source(records)
        self.assertEqual(e.export(path)['tool_history'],[])
        result=e.export(path,include_tools=True)
        self.assertEqual(len(result['tool_history']),1);self.assertNotIn('x'*32,json.dumps(result))

    def test_context_is_optional_and_repeated_human_messages_are_preserved(self):
        path=self.source([self.msg('user','Again'),self.msg('user','Again'),self.msg('user','<heartbeat>test</heartbeat>')])
        self.assertEqual(len(e.export(path)['messages']),2)
        self.assertEqual(len(e.export(path,include_context=True)['messages']),3)

    def test_unicode_line_separators_are_content_not_jsonl_boundaries(self):
        path=self.source([self.msg('user','A\u2028B\u0085C')])
        path.write_text(json.dumps(self.msg('user','A\u2028B\u0085C'),ensure_ascii=False)+'\n',encoding='utf-8')
        self.assertEqual(e.export(path)['messages'][0]['content'],'A\u2028B\u0085C')

    def test_serialized_tool_secrets_and_media_are_removed(self):
        redactor=e.Redactor()
        raw=json.dumps({'token':'UNUSUAL_SECRET_VALUE','content':[{'type':'image','data':'BASE64_MEDIA'}]})
        result=redactor.value(raw)
        self.assertNotIn('UNUSUAL_SECRET_VALUE',result);self.assertNotIn('BASE64_MEDIA',result)


if __name__=='__main__':unittest.main()
