""" Steps that run search queries.

    Queries may either be run directly via PHP using the query script
    or via the HTTP interface.
"""

import json
import os
import io
import re
from tidylib import tidy_document
import xml.etree.ElementTree as ET
import subprocess
from urllib.parse import urlencode
from collections import OrderedDict
from nose.tools import * # for assert functions

BASE_SERVER_ENV = {
    'HTTP_HOST' : 'localhost',
    'HTTP_USER_AGENT' : 'Mozilla/5.0 (X11; Linux x86_64; rv:51.0) Gecko/20100101 Firefox/51.0',
    'HTTP_ACCEPT' : 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'HTTP_ACCEPT_LANGUAGE' : 'en,de;q=0.5',
    'HTTP_ACCEPT_ENCODING' : 'gzip, deflate',
    'HTTP_CONNECTION' : 'keep-alive',
    'SERVER_SIGNATURE' : '<address>Nominatim BDD Tests</address>',
    'SERVER_SOFTWARE' : 'Nominatim test',
    'SERVER_NAME' : 'localhost',
    'SERVER_ADDR' : '127.0.1.1',
    'SERVER_PORT' : '80',
    'REMOTE_ADDR' : '127.0.0.1',
    'DOCUMENT_ROOT' : '/var/www',
    'REQUEST_SCHEME' : 'http',
    'CONTEXT_PREFIX' : '/',
    'SERVER_ADMIN' : 'webmaster@localhost',
    'REMOTE_PORT' : '49319',
    'GATEWAY_INTERFACE' : 'CGI/1.1',
    'SERVER_PROTOCOL' : 'HTTP/1.1',
    'REQUEST_METHOD' : 'GET',
    'REDIRECT_STATUS' : 'CGI'
}


def compare(operator, op1, op2):
    if operator == 'less than':
        return op1 < op2
    elif operator == 'more than':
        return op1 > op2
    elif operator == 'exactly':
        return op1 == op2
    elif operator == 'at least':
        return op1 >= op2
    elif operator == 'at most':
        return op1 <= op2
    else:
        raise Exception("unknown operator '%s'" % operator)


class SearchResponse(object):

    def __init__(self, page, fmt='json', errorcode=200):
        self.page = page
        self.format = fmt
        self.errorcode = errorcode
        self.result = []
        self.header = dict()

        if errorcode == 200:
            getattr(self, 'parse_' + fmt)()

    def parse_json(self):
        m = re.fullmatch(r'([\w$][^(]*)\((.*)\)', self.page)
        if m is None:
            code = self.page
        else:
            code = m.group(2)
            self.header['json_func'] = m.group(1)
        self.result = json.JSONDecoder(object_pairs_hook=OrderedDict).decode(code)

    def parse_html(self):
        content, errors = tidy_document(self.page,
                                        options={'char-encoding' : 'utf8'})
        #eq_(len(errors), 0 , "Errors found in HTML document:\n%s" % errors)

        b = content.find('nominatim_results =')
        e = content.find('</script>')
        content = content[b:e]
        b = content.find('[')
        e = content.rfind(']')

        self.result = json.JSONDecoder(object_pairs_hook=OrderedDict).decode(content[b:e+1])

    def parse_xml(self):
        et = ET.fromstring(self.page)

        self.header = dict(et.attrib)


        for child in et:
            assert_equal(child.tag, "place")
            self.result.append(dict(child.attrib))

    def match_row(self, row):
        if 'ID' in row.headings:
            todo = [int(row['ID'])]
        else:
            todo = range(len(self.result))

        for i in todo:
            res = self.result[i]
            for h in row.headings:
                if h == 'ID':
                    pass
                elif h == 'osm':
                    assert_equal(res['osm_type'], row[h][0])
                    assert_equal(res['osm_id'], row[h][1:])
                elif h == 'centroid':
                    x, y = row[h].split(' ')
                    assert_almost_equal(float(y), float(res['lat']))
                    assert_almost_equal(float(x), float(res['lon']))
                else:
                    assert_in(h, res)
                    assert_equal(str(res[h]), str(row[h]))


@when(u'searching for "(?P<query>.*)"(?P<dups> with dups)?')
def query_cmd(context, query, dups):
    """ Query directly via PHP script.
    """
    cmd = [os.path.join(context.nominatim.build_dir, 'utils', 'query.php'),
           '--search', query]
    # add more parameters in table form
    if context.table:
        for h in context.table.headings:
            value = context.table[0][h].strip()
            if value:
                cmd.extend(('--' + h, value))

    if dups:
        cmd.extend(('--dedupe', '0'))

    proc = subprocess.Popen(cmd, cwd=context.nominatim.build_dir,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (outp, err) = proc.communicate()

    assert_equals (0, proc.returncode, "query.php failed with message: %s\noutput: %s" % (err, outp))

    context.response = SearchResponse(outp.decode('utf-8'), 'json')


@when(u'sending (?P<fmt>\S+ )?search query "(?P<query>.*)"')
def website_search_request(context, fmt, query):
    env = BASE_SERVER_ENV

    params = { 'q' : query }
    if fmt is not None:
        params['format'] = fmt.strip()
    if context.table:
        if context.table.headings[0] == 'param':
            for line in context.table:
                params[line['param']] = line['value']
        else:
            for h in context.table.headings:
                params[h] = context.table[0][h]
    env['QUERY_STRING'] = urlencode(params)

    env['REQUEST_URI'] = '/search.php?' + env['QUERY_STRING']
    env['SCRIPT_NAME'] = '/search.php'
    env['CONTEXT_DOCUMENT_ROOT'] = os.path.join(context.nominatim.build_dir, 'website')
    env['SCRIPT_FILENAME'] = os.path.join(context.nominatim.build_dir, 'website', 'search.php')
    env['NOMINATIM_SETTINGS'] = context.nominatim.local_settings_file

    cmd = [ '/usr/bin/php-cgi', env['SCRIPT_FILENAME']]
    for k,v in params.items():
        cmd.append("%s=%s" % (k, v))

    proc = subprocess.Popen(cmd, cwd=context.nominatim.build_dir, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    (outp, err) = proc.communicate()

    assert_equals(0, proc.returncode,
                  "query.php failed with message: %s\noutput: %s" % (err, outp))

    assert_equals(0, len(err), "Unexpected PHP error: %s" % (err))

    outp = outp.decode('utf-8')

    if outp.startswith('Status: '):
        status = int(outp[8:11])
    else:
        status = 200

    content_start = outp.find('\r\n\r\n')
    assert_less(11, content_start)

    if fmt is None:
        outfmt = 'html'
    elif fmt == 'jsonv2 ':
        outfmt = 'json'
    else:
        outfmt = fmt.strip()

    context.response = SearchResponse(outp[content_start + 4:], outfmt, status)


@step(u'(?P<operator>less than|more than|exactly|at least|at most) (?P<number>\d+) results? (?:is|are) returned')
def validate_result_number(context, operator, number):
    eq_(context.response.errorcode, 200)
    numres = len(context.response.result)
    ok_(compare(operator, numres, int(number)),
        "Bad number of results: expected %s %s, got %d." % (operator, number, numres))

@then(u'a HTTP (?P<status>\d+) is returned')
def check_http_return_status(context, status):
    eq_(context.response.errorcode, int(status))

@then(u'the result is valid (?P<fmt>\w+)')
def step_impl(context, fmt):
    eq_(context.response.format, fmt)

@then(u'result header contains')
def check_header_attr(context):
    for line in context.table:
        assert_is_not_none(re.fullmatch(line['value'], context.response.header[line['attr']]),
                     "attribute '%s': expected: '%s', got '%s'"
                       % (line['attr'], line['value'],
                          context.response.header[line['attr']]))

@then(u'result header has (?P<neg>not )?attributes (?P<attrs>.*)')
def check_header_no_attr(context, neg, attrs):
    for attr in attrs.split(','):
        if neg:
            assert_not_in(attr, context.response.header)
        else:
            assert_in(attr, context.response.header)

@then(u'results contain')
def step_impl(context):
    context.execute_steps("then at least 1 result is returned")

    for line in context.table:
        context.response.match_row(line)

@then(u'result (?P<lid>\d+) has (?P<neg>not )?attributes (?P<attrs>.*)')
def validate_attributes(context, lid, neg, attrs):
    context.execute_steps("then at least %s result is returned" % lid)

    for attr in attrs.split(','):
        if neg:
            assert_not_in(attr, context.response.result[int(lid)])
        else:
            assert_in(attr, context.response.result[int(lid)])

