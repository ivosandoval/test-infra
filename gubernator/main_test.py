#!/usr/bin/env python

# Copyright 2016 The Kubernetes Authors All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
To run these tests:
    $ pip install webtest nosegae
    $ nosetests --with-gae --gae-lib-root ~/google_appengine/
"""

import os
import unittest

import webtest

import cloudstorage as gcs

import main
import gcs_async
import gcs_async_test
import view_pr

write = gcs_async_test.write

app = webtest.TestApp(main.app)


JUNIT_SUITE = '''<testsuite tests="8" failures="0" time="1000.24">
    <testcase name="First" classname="Example e2e suite" time="0">
        <skipped/>
    </testcase>
    <testcase name="Second" classname="Example e2e suite" time="36.49"/>
    <testcase name="Third" classname="Example e2e suite" time="96.49">
        <failure>/go/src/k8s.io/kubernetes/test.go:123
Error Goes Here</failure>
    </testcase>
</testsuite>'''


def init_build(build_dir, started=True, finished=True):
    """Create faked files for a build."""
    if started:
        write(build_dir + 'started.json',
              {'version': 'v1+56', 'timestamp': 1406535800})
    if finished:
        write(build_dir + 'finished.json',
              {'result': 'SUCCESS', 'timestamp': 1406536800})
    write(build_dir + 'artifacts/junit_01.xml', JUNIT_SUITE)



class TestBase(unittest.TestCase):
    def init_stubs(self):
        self.testbed.init_memcache_stub()
        self.testbed.init_app_identity_stub()
        self.testbed.init_urlfetch_stub()
        self.testbed.init_blobstore_stub()
        self.testbed.init_datastore_v3_stub()
        # redirect GCS calls to the local proxy
        gcs_async.GCS_API_URL = gcs.common.local_api_url()


class AppTest(TestBase):
    # pylint: disable=too-many-public-methods
    BUILD_DIR = '/kubernetes-jenkins/logs/somejob/1234/'

    def setUp(self):
        self.init_stubs()
        init_build(self.BUILD_DIR)

    def get_build_page(self):
        return app.get('/build' + self.BUILD_DIR)

    def test_index(self):
        """Test that the index works."""
        response = app.get('/')
        self.assertIn('kubernetes-e2e-gce', response)

    def test_missing(self):
        """Test that a missing build gives a 404."""
        response = app.get('/build' + self.BUILD_DIR.replace('1234', '1235'),
                           status=404)
        self.assertIn('1235', response)

    def test_missing_started(self):
        """Test that a missing started.json still renders a proper page."""
        build_dir = '/kubernetes-jenkins/logs/job-with-no-started/1234/'
        init_build(build_dir, started=False)
        response = app.get('/build' + build_dir)
        self.assertIn('Build Result: SUCCESS', response)
        self.assertIn('job-with-no-started', response)
        self.assertNotIn('Started', response)  # no start timestamp
        self.assertNotIn('github.com', response)  # no version => no src links

    def test_missing_finished(self):
        """Test that a missing finished.json still renders a proper page."""
        build_dir = '/kubernetes-jenkins/logs/job-still-running/1234/'
        init_build(build_dir, finished=False)
        response = app.get('/build' + build_dir)
        self.assertIn('Build Result: Not Finished', response)
        self.assertIn('job-still-running', response)
        self.assertIn('Started', response)

    def test_build(self):
        """Test that the build page works in the happy case."""
        response = self.get_build_page()
        self.assertIn('2014-07-28', response)  # started
        self.assertIn('16m40s', response)      # build duration
        self.assertIn('Third', response)       # test name
        self.assertIn('1m36s', response)       # test duration
        self.assertIn('Build Result: SUCCESS', response)
        self.assertIn('Error Goes Here', response)
        self.assertIn('test.go#L123">', response)  # stacktrace link works

    def test_build_no_failures(self):
        """Test that builds with no Junit artifacts work."""
        gcs.delete(self.BUILD_DIR + 'artifacts/junit_01.xml')
        response = self.get_build_page()
        self.assertIn('No Test Failures', response)

    def test_build_show_log(self):
        """Test that builds that failed with no failures show the build log."""
        gcs.delete(self.BUILD_DIR + 'artifacts/junit_01.xml')
        write(self.BUILD_DIR + 'finished.json',
              {'result': 'FAILURE', 'timestamp': 1406536800})

        # Unable to fetch build-log.txt, still works.
        response = self.get_build_page()
        self.assertNotIn('Error lines', response)

        self.testbed.init_memcache_stub()  # clear cached result
        write(self.BUILD_DIR + 'build-log.txt',
              u'ERROR: test \u039A\n\n\n\n\n\n\n\n\nblah'.encode('utf8'))
        response = self.get_build_page()
        self.assertIn('Error lines', response)
        self.assertIn('No Test Failures', response)
        self.assertIn('ERROR</span>: test', response)
        self.assertNotIn('blah', response)

    def test_build_failure_no_text(self):
        # Some failures don't have any associated text.
        write(self.BUILD_DIR + 'artifacts/junit_01.xml', '''
            <testsuites>
                <testsuite tests="1" failures="1" time="3.274" name="k8s.io/test/integration">
                    <testcase classname="integration" name="TestUnschedulableNodes" time="0.210">
                        <failure message="Failed" type=""/>
                    </testcase>
                </testsuite>
            </testsuites>''')
        response = self.get_build_page()
        self.assertIn('TestUnschedulableNodes', response)
        self.assertIn('junit_01.xml', response)

    def test_build_pr_link(self):
        ''' The build page for a PR build links to the PR results.'''
        build_dir = '/%s/123/e2e/567/' % view_pr.PR_PREFIX
        init_build(build_dir)
        response = app.get('/build' + build_dir)
        self.assertIn('PR #123', response)
        self.assertIn('href="/pr/123"', response)

    def test_cache(self):
        """Test that caching works at some level."""
        response = self.get_build_page()
        gcs.delete(self.BUILD_DIR + 'started.json')
        gcs.delete(self.BUILD_DIR + 'finished.json')
        response2 = self.get_build_page()
        self.assertEqual(str(response), str(response2))

    def test_build_list(self):
        """Test that the job page shows a list of builds."""
        response = app.get('/builds' + os.path.dirname(self.BUILD_DIR[:-1]))
        self.assertIn('/1234/">1234</a>', response)

    def test_job_list(self):
        """Test that the job list shows our job."""
        response = app.get('/jobs/kubernetes-jenkins/logs')
        self.assertIn('somejob/">somejob</a>', response)

    def test_nodelog_missing_files(self):
        """Test that a missing all files gives a 404."""
        build_dir = self.BUILD_DIR + 'nodelog?pod=abc'
        response = app.get('/build' + build_dir, status=404)
        self.assertIn('Unable to find', response)

    def test_nodelog_kubelet(self):
        """Test for a kubelet file with junit file.
         - missing the default kube-apiserver"""
        nodelog_url = self.BUILD_DIR + 'nodelog?pod=abc&junit=junit_01.xml'
        init_build(self.BUILD_DIR)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/junit_01.xml', JUNIT_SUITE)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/kubelet.log',
            'abc\nEvent(api.ObjectReference{Name:"abc", UID:"podabc"})\n')
        response = app.get('/build' + nodelog_url)
        self.assertIn("Wrap line", response)

    def test_nodelog_apiserver(self):
        """Test for default apiserver file
         - no kubelet file to find objrefdict
         - no file with junit file"""
        nodelog_url = self.BUILD_DIR + 'nodelog?pod=abc&junit=junit_01.xml'
        init_build(self.BUILD_DIR)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/junit_01.xml', JUNIT_SUITE)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/kube-apiserver.log',
            'apiserver pod abc\n')
        response = app.get('/build' + nodelog_url)
        self.assertIn("Wrap line", response)

    def test_nodelog_no_junit(self):
        """Test for when no junit in same folder
         - multiple folders"""
        nodelog_url = self.BUILD_DIR + 'nodelog?pod=abc&junit=junit_01.xml'
        init_build(self.BUILD_DIR)
        write(self.BUILD_DIR + 'artifacts/junit_01.xml', JUNIT_SUITE)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/kube-apiserver.log',
            'apiserver pod abc\n')
        write(self.BUILD_DIR + 'artifacts/tmp-node-2/kube-apiserver.log',
            'apiserver pod abc\n')
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/kubelet.log',
            'abc\nEvent(api.ObjectReference{Name:"abc", UID:"podabc"})\n')
        response = app.get('/build' + nodelog_url)
        self.assertIn("tmp-node-2", response)

    def test_nodelog_no_junit_apiserver(self):
        """Test for when no junit in same folder
         - multiple folders
         - no kube-apiserver.log"""
        nodelog_url = self.BUILD_DIR + 'nodelog?pod=abc&junit=junit_01.xml'
        init_build(self.BUILD_DIR)
        write(self.BUILD_DIR + 'artifacts/junit_01.xml', JUNIT_SUITE)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/docker.log',
            'Containers\n')
        write(self.BUILD_DIR + 'artifacts/tmp-node-2/kubelet.log',
            'apiserver pod abc\n')
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/kubelet.log',
            'abc\nEvent(api.ObjectReference{Name:"abc", UID:"podabc"})\n')
        response = app.get('/build' + nodelog_url)
        self.assertIn("tmp-node-2", response)

    def test_no_failed_pod(self):
        """Test that filtering page still loads when no failed pod name is given"""
        nodelog_url = self.BUILD_DIR + 'nodelog?junit=junit_01.xml'
        init_build(self.BUILD_DIR)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/junit_01.xml', JUNIT_SUITE)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/kubelet.log',
            'abc\nEvent(api.ObjectReference{Name:"abc", UID:"podabc"})\n')
        response = app.get('/build' + nodelog_url)
        self.assertIn("Wrap line", response)

    def test_parse_by_timestamp(self):
        """Test parse_by_timestamp and get_woven_logs
         - Weave separate logs together by timestamp
         - Check that lines without timestamp are combined
         - Test different timestamp formats"""
        kubelet_filepath = self.BUILD_DIR + 'artifacts/tmp-node-image/kubelet.log'
        kubeapi_filepath = self.BUILD_DIR + 'artifacts/tmp-node-image/kube-apiserver.log'
        query_string = 'nodelog?pod=abc&junit=junit_01.xml&weave=on&logfiles=%s&logfiles=%s' % (
            kubelet_filepath, kubeapi_filepath)
        nodelog_url = self.BUILD_DIR + query_string
        init_build(self.BUILD_DIR)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/junit_01.xml', JUNIT_SUITE)
        write(kubelet_filepath,
            'abc\n0101 01:01:01.001 Event(api.ObjectReference{Name:"abc", UID:"podabc"})\n')
        write(kubeapi_filepath,
            '0101 01:01:01.000 kubeapi\n0101 01:01:01.002 pod\n01-01T01:01:01.005Z last line')
        expected = ('0101 01:01:01.000 kubeapi\n'
                    '<span class="hilight">abc0101 01:01:01.001 Event(api.ObjectReference{Name:'
                    '&#34;<span class="keyword">abc</span>&#34;, UID:&#34;podabc&#34;})</span>\n'
                    '0101 01:01:01.002 pod\n'
                    '01-01T01:01:01.005Z last line')
        response = app.get('/build' + nodelog_url)
        print response
        self.assertIn(expected, response)

    def test_timestamp_no_apiserver(self):
        """Test parse_by_timestamp and get_woven_logs
         - Weave separate logs together by timestamp
         - Check that lines without timestamp are combined
         - Test different timestamp formats
         - no kube-apiserver.log"""
        kubelet_filepath = self.BUILD_DIR + 'artifacts/tmp-node-image/kubelet.log'
        proxy_filepath = self.BUILD_DIR + 'artifacts/tmp-node-image/kube-proxy.log'
        query_string = 'nodelog?pod=abc&junit=junit_01.xml&weave=on&logfiles=%s&logfiles=%s' % (
            kubelet_filepath, proxy_filepath)
        nodelog_url = self.BUILD_DIR + query_string
        init_build(self.BUILD_DIR)
        write(self.BUILD_DIR + 'artifacts/tmp-node-image/junit_01.xml', JUNIT_SUITE)
        write(kubelet_filepath,
            'abc\n0101 01:01:01.001 Event(api.ObjectReference{Name:"abc", UID:"podabc"})\n')
        write(proxy_filepath,
            '0101 01:01:01.000 proxy\n0101 01:01:01.002 pod\n01-01T01:01:01.005Z last line')
        expected = ('0101 01:01:01.000 proxy\n'
                    '<span class="hilight">abc0101 01:01:01.001 Event(api.ObjectReference{Name:'
                    '&#34;<span class="keyword">abc</span>&#34;, UID:&#34;podabc&#34;})</span>\n'
                    '0101 01:01:01.002 pod\n'
                    '01-01T01:01:01.005Z last line')
        response = app.get('/build' + nodelog_url)
        self.assertIn(expected, response)


class PRTest(TestBase):
    BUILDS = {
        'build': [('12', {'version': 'bb', 'timestamp': 1467147654}, None),
                  ('11', {'version': 'bb', 'timestamp': 1467146654}, {'result': 'PASSED'}),
                  ('10', {'version': 'aa', 'timestamp': 1467136654}, {'result': 'FAILED'})],
        'e2e': [('47', {'version': 'bb', 'timestamp': '1467147654'}, {'result': '[UNSET]'}),
                ('46', {'version': 'aa', 'timestamp': '1467136700'}, {'result': '[UNSET]'})]
    }

    def setUp(self):
        self.init_stubs()

    def init_pr_directory(self):
        gcs_async_test.install_handler(self.testbed.get_stub('urlfetch'),
            {'123/': ['build', 'e2e'],
             '123/build/': ['11', '10', '12'],  # out of order
             '123/e2e/': ['47', '46']})

        for job, builds in self.BUILDS.iteritems():
            for build, started, finished in builds:
                path = '/%s/123/%s/%s/' % (view_pr.PR_PREFIX, job, build)
                if started:
                    write(path + 'started.json', started)
                if finished:
                    write(path + 'finished.json', finished)

    def test_pr_builds(self):
        self.init_pr_directory()
        builds = view_pr.pr_builds('123')
        self.assertEqual(builds, self.BUILDS)

    def test_pr_handler(self):
        self.init_pr_directory()
        response = app.get('/pr/123')
        self.assertIn('e2e/47', response)
        self.assertIn('PASSED', response)
        self.assertIn('colspan="3"', response)  # header
        self.assertIn('github.com/kubernetes/kubernetes/pull/123', response)
        self.assertIn('28 20:44', response)

    def test_pr_handler_missing(self):
        gcs_async_test.install_handler(self.testbed.get_stub('urlfetch'),
            {'124/': []})
        response = app.get('/pr/124')
        self.assertIn('No Results', response)

    def test_pr_build_log_redirect(self):
        path = '123/some-job/55/build-log.txt'
        response = app.get('/pr/' + path)
        self.assertEqual(response.status_code, 302)
        self.assertIn('https://storage.googleapis.com', response.location)
        self.assertIn(path, response.location)
