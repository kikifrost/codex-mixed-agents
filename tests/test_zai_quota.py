"""Quota interpretation cases, from the accepted spec, with synthetic data only."""
import importlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/mixed-agents/bridge'))

def quota_row(**changes):
    row={'type':'CREDIT_LIMIT','unit':3,'number':5,'percentage':20}
    row.update(changes)
    return {'data':{'limits':[row]}}

class ZaiQuotaParsingTests(unittest.TestCase):
    def parse(self,payload):
        return importlib.import_module('zai').parse_usage(payload)
    def remaining(self,payload):
        return self.parse(payload)['remaining_percent']
    def test_used_percentage_is_inverted(self):
        self.assertEqual(self.remaining(quota_row(percentage=89)),11)
        self.assertEqual(self.remaining(quota_row(percentage=90)),10)
        self.assertEqual(self.remaining(quota_row(percentage=100)),0)
        self.assertEqual(self.remaining(quota_row(percentage=0)),100)
    def test_legacy_tokens_and_credit_five_hour_rows(self):
        for typ in ('CREDIT_LIMIT','TOKENS_LIMIT'):
            self.assertEqual(self.remaining(quota_row(type=typ,percentage=25)),75)
    def test_precise_ratio_and_greater_used_percentage_are_conservative(self):
        self.assertAlmostEqual(self.remaining(quota_row(percentage=89,currentValue=8999,usage=10000)),10.01)
        self.assertEqual(self.remaining(quota_row(percentage=91,currentValue=8999,usage=10000)),9)
        self.assertAlmostEqual(self.remaining(quota_row(percentage=None,currentValue=8999,usage=10000)),10.01)
    def test_weekly_never_substitutes_for_five_hour(self):
        for fields in ({'unit':6,'number':1},{'unit':3,'number':168},{'unit':3,'number':1}):
            self.assertIsNone(self.remaining(quota_row(**fields)))
        payload=quota_row(percentage=90)
        payload['data']['limits'].append({'type':'CREDIT_LIMIT','unit':6,'number':1,'percentage':1})
        self.assertEqual(self.remaining(payload),10)
    def test_malformed_missing_nonfinite_out_of_range_are_unknown(self):
        for value in (None,True,-1,101,float('nan'),float('inf'),10**400,'garbage'):
            with self.subTest(value=value): self.assertIsNone(self.remaining(quota_row(percentage=value)))
        for payload in ({},None,[],{'data':{}},{'data':{'limits':[]}},{'data':{'limits':[None]}}):
            with self.subTest(payload=payload): self.assertIsNone(self.remaining(payload))
    def test_provider_error_envelopes_never_authorize_usage(self):
        for error in ({'success':False}, {'code':401}, {'code':500}):
            payload=quota_row(percentage=1)
            payload.update(error)
            self.assertIsNone(self.remaining(payload))
    def test_invalid_ratio_cannot_create_zero_usage(self):
        for current,total in ((-1,100),(101,100),(0,0),(float('nan'),100),(10,float('inf'))):
            self.assertIsNone(self.remaining(quota_row(percentage=None,currentValue=current,usage=total)))
    def test_multiple_five_hour_rows_take_lowest_remaining(self):
        payload=quota_row(percentage=20)
        payload['data']['limits'].append({'type':'TOKENS_LIMIT','unit':3,'number':5,'percentage':95})
        self.assertEqual(self.remaining(payload),5)


class ZaiGuardDecisionTests(unittest.TestCase):
    def test_reserve_boundary_and_disabled_behavior(self):
        zai=importlib.import_module('zai')
        policy=zai.normalize_policy({'usage_guard':True})
        for remaining in (0,5,9.999,10):
            self.assertEqual(zai.block_reason({'available':True,'remaining_percent':remaining},policy),'provider_quota_reserve')
        for remaining in (10.001,11,100):
            self.assertIsNone(zai.block_reason({'available':True,'remaining_percent':remaining},policy))
        self.assertIsNone(zai.block_reason({},zai.normalize_policy({})))
    def test_untrustworthy_readings_fail_closed(self):
        zai=importlib.import_module('zai')
        policy=zai.normalize_policy({'usage_guard':True})
        for usage in ({},{'available':False,'remaining_percent':99},{'available':True,'remaining_percent':None},{'available':True,'remaining_percent':float('nan')},{'available':True,'remaining_percent':float('inf')},{'available':True,'remaining_percent':101},{'available':True,'remaining_percent':-1},{'available':True,'remaining_percent':True},{'available':True,'remaining_percent':'99'}):
            with self.subTest(usage=usage): self.assertEqual(zai.block_reason(usage,policy),'provider_usage_unavailable')
    def test_unknown_policy_fields_and_stored_null_are_rejected(self):
        zai=importlib.import_module('zai')
        for raw in ({'extra':'field'},{'limit':None},{'usage_guard':None},{'stop_remaining_percent':None}):
            with self.assertRaises(ValueError): zai.normalize_policy(raw)

if __name__=='__main__': unittest.main()
