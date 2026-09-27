#!/usr/bin/env python3
import json
import urllib.request

URL = "https://park.auralith.ru/api/fuel-stations?west=30.15&south=59.70&east=30.55&north=59.95&detail=full"
req = urllib.request.Request(URL, headers={"User-Agent":"Mozilla/5.0 FuelMonitor/1.0","Accept":"application/json"})
with urllib.request.urlopen(req, timeout=45) as r:
    body = r.read().decode("utf-8","replace")
    print("STATUS", r.status, "LEN", len(body))
    print(body[:20000])
