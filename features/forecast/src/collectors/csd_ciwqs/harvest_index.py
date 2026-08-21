import json, re, time, requests

docs = json.load(open('smr_documents.json'))
FAC = {'256498': 'OSP', '256499': 'SEP'}
sess = requests.Session()
sess.headers['User-Agent'] = 'Mozilla/5.0 (research; CSD records compilation)'

index = []
n = 0
for yr, recs in sorted(docs.items(), key=lambda x: int(x[0])):
    for r in recs:
        docid = r['smr_document_id']
        url = f"https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/PublicReportEsmrAtGlanceServlet?reportID=2&isDrilldown=true&documentID={docid}"
        try:
            html = sess.get(url, timeout=60).text
        except Exception as e:
            print('ERR', docid, e); continue
        atts = re.findall(r"PublicAttachmentRetriever\?parentID=(\d+)&(?:amp;)?attachmentID=(\d+)&(?:amp;)?attType=(\d)'\s*>([^<]+)<", html)
        index.append({'year': yr, 'facility': FAC[r['facility_place_id']],
                      'report_name': r['report_name'], 'document_id': docid,
                      'attachments': [{'parentID': a[0], 'attachmentID': a[1], 'attType': a[2], 'name': a[3].strip()} for a in atts]})
        n += 1
        if n % 25 == 0: print(f"{n} drilldowns fetched...")
        time.sleep(0.25)
json.dump(index, open('attachment_index.json','w'), indent=1)
print('DONE', len(index), 'documents indexed')
