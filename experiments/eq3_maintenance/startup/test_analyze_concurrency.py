import csv,tempfile,unittest
from pathlib import Path
from analyze_concurrency import analyze

class ConcurrencyFactsTest(unittest.TestCase):
    def test_counts_operations_commands_and_overlap_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with (root/'requests.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['phase','request_id','arrival_ns','completion_ns','bytes'])
                w.writeheader();w.writerows([dict(phase='STARTUP_WRITE',request_id=i,arrival_ns=0,completion_ns=20,bytes=4096) for i in (1,2)])
            with (root/'request-observations.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['device_outstanding']);w.writeheader();w.writerows([{'device_outstanding':1},{'device_outstanding':2}])
            fields=['external_request_id','maintenance_request_id','type','phase','command_id','time_ns','transaction_id','channel','chip','die','plane']
            with (root/'native-events.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
                for command,rid,channel in ((10,1,0),(11,2,1)):
                    for phase,time in ((1,5),(2,15)):
                        w.writerow(dict(external_request_id=rid,maintenance_request_id=0,type=1,phase=phase,
                                        command_id=command,time_ns=time,transaction_id=100+rid,
                                        channel=channel,chip=0,die=0,plane=0))
            result=analyze(root)
            self.assertEqual(result['unique_program_transactions'],2)
            self.assertEqual(result['program_commands'],2)
            self.assertEqual(result['max_concurrent_program_commands'],2)
            self.assertEqual(result['max_active_channel_chip_die_resources'],2)
            self.assertTrue(result['concurrency_verified'])

if __name__=='__main__':unittest.main()
