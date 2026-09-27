#!/usr/bin/env python3
import ssl, urllib.request, urllib.error
URL="https://park.auralith.ru/api/fuel-stations?west=30.15&south=59.70&east=30.55&north=59.95&detail=full"
req=urllib.request.Request(URL,headers={"User-Agent":"Mozilla/5.0 FuelMonitor/1.0","Accept":"application/json"})
ctx=ssl._create_unverified_context()
try:
    with urllib.request.urlopen(req,timeout=45,context=ctx) as r:
        body=r.read().decode("utf-8","replace")
        print("STATUS",r.status,"HEADERS",dict(r.headers),"LEN",len(body))
        print(body[:30000])
except urllib.error.HTTPError as e:
    body=e.read().decode("utf-8","replace")
    print("HTTPERR",e.code,"HEADERS",dict(e.headers),"BODY",body[:10000])
    raise
