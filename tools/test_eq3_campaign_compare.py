import csv,tempfile,unittest
from pathlib import Path
from eq3_campaign_compare import compare,crossings

class CompareTests(unittest.TestCase):
    def test_crossings_include_cooling_and_declared_initial(self):
        self.assertEqual(crossings([(1,302),(2,300)],301),[('up',.5),('down',1.5)])
    def test_normalization_not_absolute_temperature(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a';b=Path(tmp)/'b'
            for path,offset in [(a,0),(b,.2)]:
                with path.open('w',newline='') as f:
                    w=csv.writer(f);w.writerow(['time_s','sensor_id','temperature_k']);w.writerows([[.1,'component:a:mean',300+offset],[.2,'component:a:mean',300.1+offset]])
            r=compare(a,b,'rc');self.assertEqual(r['status'],'NUMERICAL_FAIL');self.assertAlmostEqual(r['sensors'][0]['normalized_mae'],.2)
            self.assertEqual(compare(a,b,'reference')['status'],'PASS')

if __name__=='__main__':unittest.main()
