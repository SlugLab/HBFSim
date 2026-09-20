import csv,tempfile,unittest
from pathlib import Path
from eq3_campaign_compare import compare,crossings

class CompareTests(unittest.TestCase):
    def test_crossings_include_cooling_and_declared_initial(self):
        self.assertEqual(crossings([(1,302),(2,300)],301),[('up',.5),('down',1.5)])
        self.assertEqual(crossings([(1,312),(2,310)],311,310),
                         [('up',.5),('down',1.5)])
    def test_normalization_not_absolute_temperature(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a';b=Path(tmp)/'b'
            for path,offset in [(a,0),(b,.2)]:
                with path.open('w',newline='') as f:
                    w=csv.writer(f);w.writerow(['time_s','sensor_id','temperature_k']);w.writerows([[.1,'component:a:mean',300+offset],[.2,'component:a:mean',300.1+offset]])
            r=compare(a,b,'rc');self.assertEqual(r['status'],'NUMERICAL_FAIL');self.assertAlmostEqual(r['sensors'][0]['normalized_mae'],.2)
            self.assertEqual(r['analysis_initial_k'],300)
            self.assertEqual(r['analysis_probes_k'],[301,330])
            self.assertEqual(compare(a,b,'reference')['status'],'PASS')

    def test_explicit_initial_and_probes_are_reported_and_consumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a';b=Path(tmp)/'b'
            for path in (a,b):
                with path.open('w',newline='') as f:
                    w=csv.writer(f);w.writerow(['time_s','sensor_id','temperature_k'])
                    w.writerows([[.1,'component:a:mean',311],[.2,'component:a:mean',312]])
            r=compare(a,b,'rc',initial_k=310,probes_k=(310.5,311.5))
            self.assertEqual(r['analysis_initial_k'],310)
            self.assertEqual(r['analysis_probes_k'],[310.5,311.5])
            self.assertEqual([x['status'] for x in r['sensors'][0]['crossings']],
                             ['PASS','PASS'])

if __name__=='__main__':unittest.main()
