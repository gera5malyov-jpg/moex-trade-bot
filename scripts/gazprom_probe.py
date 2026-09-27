#!/usr/bin/env python3
import ssl, json, urllib.request, urllib.error, http.cookiejar

BASE="https://park.auralith.ru"
ctx=ssl._create_unverified_context()
cj=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.HTTPSHandler(context=ctx))

def call(req):
    try:
        with opener.open(req,timeout=45) as r:
            body=r.read().decode("utf-8","replace")
            print("STATUS",req.full_url,r.status,"HEADERS",dict(r.headers),"BODY",body[:30000])
            return r.status, body
    except urllib.error.HTTPError as e:
        body=e.read().decode("utf-8","replace")
        print("HTTPERR",req.full_url,e.code,"HEADERS",dict(e.headers),"BODY",body[:10000])
        return e.code, body

sreq=urllib.request.Request(BASE+"/api/session/init",data=b"",method="POST",headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
status,body=call(sreq)
token=""
try: token=json.loads(body).get("token","")
except Exception: pass
print("TOKEN_PRESENT",bool(token))
url=BASE+"/api/fuel-stations?west=30.15&south=59.70&east=30.55&north=59.95&detail=full"
headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
if token: headers["X-Api-Token"]=token
call(urllib.request.Request(url,headers=headers))
