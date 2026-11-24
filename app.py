"""Invoice extraction -> schema validation -> human review -> SQLite/CSV export."""
import csv,hashlib,io,json,os,re,sqlite3,uuid
from datetime import date
from decimal import Decimal,InvalidOperation
from pathlib import Path
from fastapi import FastAPI,HTTPException,UploadFile,File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field,field_validator
from pypdf import PdfReader
DB=Path(os.getenv('DB_PATH',str(Path(__file__).parent/'documents.db')))
app=FastAPI(title='Document Intelligence Pipeline')
def connect():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
 c.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, digest TEXT UNIQUE, name TEXT, payload TEXT, status TEXT, error TEXT)')
 c.commit();return c
class Invoice(BaseModel):
 vendor:str=Field(min_length=1)
 invoice_number:str=Field(min_length=1)
 invoice_date:date
 total:Decimal=Field(ge=0)
 currency:str=Field(default='USD',pattern='^[A-Z]{3}$')
 @field_validator('total')
 @classmethod
 def money(cls,v):
  if v.as_tuple().exponent < -2:raise ValueError('At most two decimal places')
  return v
class TextInput(BaseModel):
 name:str
 text:str=Field(min_length=3)
class Review(BaseModel):
 accept:bool
 corrected:Invoice|None=None

def parse_mock(text):
 """Regex baseline for demo samples. Unreadable data must be reviewed, not silently guessed."""
 def field(pattern):
  match=re.search(pattern,text,re.I|re.M)
  return match.group(1).strip() if match else None
 return {'vendor':field(r'^Vendor:\s*(.+)$'),'invoice_number':field(r'^Invoice(?: Number| #)?:\s*(.+)$'),'invoice_date':field(r'^Date:\s*(\d{4}-\d{2}-\d{2})'),'total':field(r'^Total:\s*\$?([\d,.]+)'),'currency':'USD'}
def extract(text):
 if os.getenv('OPENAI_API_KEY'):
  resp=__import__('openai').OpenAI().chat.completions.create(model=os.getenv('OPENAI_MODEL','gpt-4o-mini'),temperature=0,response_format={'type':'json_object'},messages=[{'role':'system','content':'Extract invoice fields vendor, invoice_number, invoice_date YYYY-MM-DD, total, currency. Return JSON only. Missing values must be null. Do not guess.'},{'role':'user','content':text[:16000]}])
  return json.loads(resp.choices[0].message.content)
 return parse_mock(text)
def ingest(name,text):
 digest=hashlib.sha256(text.encode()).hexdigest();c=connect()
 existing=c.execute('SELECT id,status FROM documents WHERE digest=?',(digest,)).fetchone()
 if existing:c.close();return {'id':existing['id'],'status':existing['status'],'duplicate':True}
 id=str(uuid.uuid4());raw=extract(text)
 try:
  record=Invoice.model_validate(raw)
  payload=record.model_dump(mode='json');status='needs_review';error='Human approval required before final export'
 except Exception as e:
  payload=raw;status='invalid';error=str(e)
 with c:c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',(id,digest,name,json.dumps(payload,default=str),status,error))
 c.close();return {'id':id,'status':status,'duplicate':False,'extracted':payload,'error':error}
@app.post('/documents/text')
def add_text(req:TextInput):return ingest(req.name,req.text)
@app.post('/documents/pdf')
async def add_pdf(file:UploadFile=File(...)):
 if not (file.filename or '').lower().endswith('.pdf'):raise HTTPException(400,'PDF only')
 b=await file.read()
 if len(b)>10_000_000:raise HTTPException(413,'File too large')
 try:text='\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(b)).pages)
 except Exception as exc:raise HTTPException(422,'Invalid PDF') from exc
 if not text.strip():raise HTTPException(422,'OCR needed for image-only PDF')
 return ingest(file.filename,text)
@app.get('/documents/{id}')
def get(id:str):
 c=connect();r=c.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone();c.close()
 if not r:raise HTTPException(404,'Not found')
 return {'id':r['id'],'name':r['name'],'data':json.loads(r['payload']),'status':r['status'],'error':r['error']}
@app.post('/documents/{id}/review')
def review(id:str,body:Review):
 d=get(id)
 if not body.accept:status='rejected';payload=d['data']
 else:
  try:payload=(body.corrected or Invoice.model_validate(d['data'])).model_dump(mode='json')
  except Exception as e:raise HTTPException(422,str(e))
  status='approved'
 c=connect()
 with c:c.execute('UPDATE documents SET payload=?,status=?,error=? WHERE id=?',(json.dumps(payload),status,None,id))
 c.close();return get(id)
@app.get('/export.csv')
def export():
 c=connect();rows=c.execute('SELECT * FROM documents WHERE status="approved"').fetchall();c.close()
 buf=io.StringIO();writer=csv.writer(buf);writer.writerow(['document_id','vendor','invoice_number','invoice_date','total','currency'])
 for r in rows:
  x=json.loads(r['payload']);writer.writerow([r['id'],x['vendor'],x['invoice_number'],x['invoice_date'],x['total'],x['currency']])
 buf.seek(0);return StreamingResponse(iter([buf.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="approved_invoices.csv"'})
