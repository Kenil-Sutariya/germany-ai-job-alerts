import unittest
from monitor import get_jobs,normalized,job_id,fit_score,application_url

class MonitorTests(unittest.TestCase):
    def test_nested_schema(self):
        data={'jobs':[{'title':'Junior AI Engineer','url':'https://acme.de/jobs/1','company':'Acme'}]}
        self.assertEqual(len(get_jobs(data)),1)
    def test_matching(self):
        j=normalized({'title':'Junior Machine Learning Engineer','url':'https://jobs.lever.co/example/1','description':'Python and PyTorch'})
        self.assertGreaterEqual(fit_score(j),0)
        self.assertTrue(application_url(j['url']))
    def test_senior_rejected(self):
        j=normalized({'title':'Senior AI Engineer','url':'https://acme.de/jobs/1'})
        self.assertEqual(fit_score(j),-1)
    def test_url_dedup(self):
        a=normalized({'title':'Data Scientist','url':'https://acme.de/job/1?utm_source=github'})
        b=normalized({'title':'Data Scientist','url':'https://acme.de/job/1'})
        self.assertEqual(job_id(a),job_id(b))
    def test_linkedin_rejected(self):
        self.assertFalse(application_url('https://linkedin.com/jobs/view/123'))

if __name__=='__main__':
    unittest.main()
