#!/usr/bin/env python3
"""Analytical test fixture, independent of the instrumenter's timing helpers."""
import json
L0=.5
L1=5.0
cases=[]
for work,expected in [(6.0,0.0),(2.0,3.0),(.2,4.5)]:
    result=max(0,L1-work)-max(0,L0-work)
    assert abs(result-expected)<1e-12
    cases.append({'L0_us':L0,'L1_us':L1,'W_us':work,'DeltaS_us':result})
print(json.dumps({'scope':'ANALYTICAL_TEST_FIXTURE_NOT_MEASURED','cases':cases,'passed':True},indent=2))
