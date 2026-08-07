#!/usr/bin/env python3
"""Build a FABRICATED Paytm-shaped extraction and a case from it.

Every value here is invented. Nothing derives from a real device, so the resulting case —
and any screenshot of it — can be published safely. It exists so the README can show real
screenshots without exposing anyone's data, and so anyone can try the tool without an
extraction of their own.

The shapes mirror the real schemas closely enough to exercise the parsers, the WAL path,
the carver, entity correlation and every GUI view.

Usage:
    python3 tools/make_demo_case.py <out_dir>
      -> <out_dir>/net.one97.paytm   fabricated extraction
      -> <out_dir>/case              parsed case (case.db, report.html, ...)
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SEED = 20260731
MS_DAY = 86_400_000
T0 = 1_735_689_600_000          # 2025-01-01T00:00:00Z

# ---- fabricated cast -------------------------------------------------------- #
SUBJECT = {"name": "Priya Demo", "phone": "9000000001", "cid": "9900000001",
           "sb": "1900000000000000001"}
MERCHANTS = [
    ("Acme Foods Pvt Ltd", "acme-1001@demobank", "5555_HDFC0DEMO01", "savings", "🥘 Food"),
    ("Acmefoods Private Limited", "acme-1002@demobank", "5555_HDFC0DEMO01", "savings", "🥘 Food"),
    ("QuickEats", "quickeats@demopay", "5555_HDFC0DEMO01", "savings", "🥘 Food"),
    ("Quickeats Ltd", "quickeats.biz@demopay", "5555_HDFC0DEMO01", "savings", "🥘 Food"),
    ("Bistro Online", "bistro@demoupi", "6666_ICIC0DEMO02", "savings", "🥘 Food"),
    ("CityMart Retail", "citymart@demoupi", "6666_ICIC0DEMO02", "savings", "🛒 Shopping"),
]
PEOPLE = [
    ("Rahul Example", "9000000002", "9900000002", "1900000000000000002"),
    ("Sana Sample", "9000000003", "9900000003", "1900000000000000003"),
    ("Vikram Test", "9000000004", "9900000004", "1900000000000000004"),
]


def _db(path, wal=False):
    con = sqlite3.connect(path)
    con.execute("PRAGMA auto_vacuum=NONE")
    con.execute("PRAGMA secure_delete=OFF")
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    return con


def build_extraction(root: str) -> None:
    rnd = random.Random(SEED)
    dbd, sp, nb = (os.path.join(root, d) for d in ("databases", "shared_prefs", "no_backup"))
    for d in (dbd, sp, nb, os.path.join(root, "app_uthDir"), os.path.join(root, "shared_jsons")):
        os.makedirs(d, exist_ok=True)

    # ---- passbook: some rows checkpointed, the newest left in the -wal ------- #
    live = os.path.join(root, "_stage"); os.makedirs(live, exist_ok=True)
    pb = os.path.join(live, "passbook.db")
    con = _db(pb, wal=True)
    con.execute("""CREATE TABLE UthListingEntity(
        txnId TEXT, sourceTxnId TEXT PRIMARY KEY, amount REAL, txnIndicator INTEGER,
        identifier TEXT, secondPartyInfo TEXT, statusKey INTEGER, narration TEXT,
        streamSource TEXT, txnCategory INTEGER, txnDate INTEGER, txnTag TEXT,
        userInstrumentInfo TEXT, errorCode TEXT, searchableStrings TEXT,
        mobileNumber TEXT, remarks TEXT)""")
    con.execute("""CREATE TABLE UthInstrumentEntity(
        sourceTxnId TEXT, instrumentName TEXT, identifier TEXT, accountType TEXT,
        instrumentType INTEGER, id INTEGER PRIMARY KEY AUTOINCREMENT)""")

    def add_txn(i, day, checkpointed):
        name, vpa, acct, atype, tag = MERCHANTS[i % len(MERCHANTS)]
        amt = round(rnd.uniform(20, 450), 2)
        rrn = f"{rnd.randint(10**11, 10**12 - 1)}"
        sid = f"DEMO{i:08d}"
        status = 2 if i % 11 else 1                 # one pending in every eleven
        con.execute("INSERT INTO UthListingEntity VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"TXN{i:06d}", sid, amt, 2, vpa, json.dumps({"name": name}), status,
                     f"Paid to {name}", "UPI", 1, T0 + day * MS_DAY, tag,
                     json.dumps([{"identifier": acct, "accountType": atype,
                                  "instrumentName": "Demo Bank"}]),
                     None, f"{name},{vpa},{rrn}", None, None))
        con.execute("INSERT INTO UthInstrumentEntity(sourceTxnId,instrumentName,identifier,"
                    "accountType,instrumentType) VALUES(?,?,?,?,?)",
                    (sid, "Demo Bank", acct, atype, 1))

    for i in range(34):
        add_txn(i, i * 5, True)
    # a couple of incoming transfers from people
    for j, (pname, phone, _cid, _sb) in enumerate(PEOPLE[:2]):
        sid = f"DEMOIN{j:06d}"
        con.execute("INSERT INTO UthListingEntity VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"TXNIN{j}", sid, round(rnd.uniform(500, 2500), 2), 1, None,
                     json.dumps({"name": pname}), 2, f"Received from {pname}", "UPI", 2,
                     T0 + (40 + j * 9) * MS_DAY, "💵 Money Received",
                     json.dumps([{"identifier": "5555_HDFC0DEMO01", "accountType": "savings",
                                  "instrumentName": "Demo Bank"}]),
                     None, f"{pname},{phone},{rnd.randint(10**11, 10**12-1)}", phone, None))
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # the most recent 6 stay ONLY in the WAL — this is what F-01 was about
    for i in range(34, 40):
        add_txn(i, 200 + (i - 34) * 4, False)
    con.commit()
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(pb + suf):
            shutil.copy2(pb + suf, os.path.join(dbd, "passbook.db" + suf))
    con.close()

    # ---- chatDb ------------------------------------------------------------- #
    con = _db(os.path.join(dbd, "chatDb.db"))
    con.execute("""CREATE TABLE TBL_USERS(isMe INTEGER, sendbirdUserName TEXT,
        sendbirdUserId TEXT, phoneNumber TEXT, identifier TEXT, type TEXT, vpa TEXT,
        name TEXT, bankName TEXT, maskedAccNo TEXT, verifiedName TEXT, getInfoSync TEXT,
        getPaymentInfoSync TEXT, userPrimaryKey INTEGER PRIMARY KEY AUTOINCREMENT)""")
    age = json.dumps({"jsonString": json.dumps({"customerCreationText": "On Paytm Since Mar 2022"})})
    con.execute("INSERT INTO TBL_USERS(isMe,sendbirdUserName,sendbirdUserId,phoneNumber,"
                "identifier,type,name,getInfoSync) VALUES(1,?,?,?,?,'CUSTOMER',?,?)",
                (SUBJECT["name"], SUBJECT["sb"], SUBJECT["phone"], SUBJECT["cid"],
                 SUBJECT["name"], age))
    for pname, phone, cid, sb in PEOPLE:
        con.execute("INSERT INTO TBL_USERS(isMe,sendbirdUserName,sendbirdUserId,phoneNumber,"
                    "identifier,type,name,getInfoSync) VALUES(0,?,?,?,?,'CUSTOMER',?,?)",
                    (pname, sb, phone, cid, pname, age))
    for name, vpa, *_ in MERCHANTS:
        con.execute("INSERT INTO TBL_USERS(isMe,sendbirdUserName,sendbirdUserId,phoneNumber,"
                    "identifier,type,vpa,name) VALUES(0,?,?,NULL,?,'MERCHANT',?,?)",
                    (name, f"SB-{abs(hash(name)) % 10**12}", f"MID{abs(hash(name)) % 10**6}",
                     vpa, name))
    con.execute("""CREATE TABLE TBL_CHANNELS(channelUrl TEXT PRIMARY KEY, name TEXT,
        createdAt INTEGER, lastMessageAt INTEGER)""")
    con.execute("""CREATE TABLE DBChannelUserEntryCrossRef(channelUrl TEXT, userPrimaryKey INTEGER)""")
    con.execute("""CREATE TABLE ChatMessageEntity(id INTEGER PRIMARY KEY AUTOINCREMENT,
        data TEXT, messageContent TEXT, senderName TEXT, senderId TEXT, createdAt INTEGER,
        customType TEXT, channelUrl TEXT, messageState INTEGER, messageType TEXT,
        rawMessage BLOB)""")
    con.execute("CREATE TABLE TBL_MESSAGE_HISTORY(channelUrl TEXT, lastSeen INTEGER)")
    for k, (pname, phone, cid, sb) in enumerate(PEOPLE):
        chan = f"demo_channel_{k}"
        con.execute("INSERT INTO TBL_CHANNELS VALUES(?,?,?,?)",
                    (chan, pname, T0 + k * MS_DAY, T0 + (k + 30) * MS_DAY))
        con.execute("INSERT INTO DBChannelUserEntryCrossRef VALUES(?,?)", (chan, 1))
        con.execute("INSERT INTO DBChannelUserEntryCrossRef VALUES(?,?)", (chan, k + 2))
        for m in range(6):
            outgoing = m % 2 == 0
            amt = round(rnd.uniform(50, 400), 2)
            status = "SUCCESS" if m % 5 else "COMPLETED"
            ctype = "TRANSFER" if m % 5 else "UPI_REQUEST"
            con.execute("INSERT INTO ChatMessageEntity(data,messageContent,senderName,"
                        "senderId,createdAt,customType,channelUrl,messageState,messageType,"
                        "rawMessage) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (json.dumps({"amount": str(amt), "rrn": f"{rnd.randint(10**11,10**12-1)}",
                                     "msgStatus": status, "paymentMode": "UPI",
                                     "uniqueKey": f"CHAT{k}{m:04d}"}),
                         f"Sent you ₹{amt:g}", SUBJECT["name"] if outgoing else pname,
                         SUBJECT["sb"] if outgoing else sb,
                         T0 + (k * 30 + m * 3) * MS_DAY, ctype, chan, 6, ctype, b"\x01\x02"))
    # two conversations whose messages are gone — they must still be visible
    for k in (90, 91):
        con.execute("INSERT INTO TBL_CHANNELS VALUES(?,?,?,?)",
                    (f"demo_channel_{k}", f"Archived Contact {k-89}", T0, T0))
    con.commit(); con.close()

    # ---- supporting stores -------------------------------------------------- #
    con = _db(os.path.join(dbd, "ups_database"))
    con.execute("CREATE TABLE ConsentTable(consentKey TEXT PRIMARY KEY, consentValue TEXT,"
                " syncedWithServer INTEGER, verticalId TEXT, syncTimestamp INTEGER)")
    for key in ("universal.sms_read_consent", "contact_management.contact_sync_consent",
                "security.shield_consent", "whatsapp.notify_consent"):
        con.execute("INSERT INTO ConsentTable VALUES(?,?,?,?,?)",
                    (f"ocl.permission.{key}", "true", 1, "-1", T0 + 3 * MS_DAY))
    con.commit(); con.close()

    # A plausible movement history: a daily Bengaluru pattern (home / office / mall),
    # a domestic trip to Hyderabad, and one to Mumbai. Invented places, real-world
    # geography, so the map view shows something an examiner would recognise instead of
    # a synthetic straight line.
    PLACES = [
        ("home",      12.9352, 77.6245),   # Koramangala, Bengaluru
        ("office",    12.9698, 77.7500),   # Whitefield, Bengaluru
        ("mall",      12.9784, 77.6408),   # MG Road, Bengaluru
        ("cafe",      12.9279, 77.6271),   # BTM Layout, Bengaluru
        ("airport",   13.1986, 77.7066),   # Kempegowda International
        ("hyderabad", 17.4435, 78.3772),   # HITEC City
        ("mumbai",    19.0760, 72.8777),   # Mumbai
    ]
    #: (place, day-offset) — commute pattern, then two trips
    TRACK = ([("home", d) for d in range(10, 40, 6)]
             + [("office", d) for d in range(11, 41, 6)]
             + [("cafe", 14), ("mall", 20), ("mall", 33)]
             + [("airport", 45), ("hyderabad", 46), ("hyderabad", 47), ("airport", 48)]
             + [("airport", 70), ("mumbai", 71), ("mumbai", 72), ("airport", 74)]
             + [("home", d) for d in (50, 55, 60, 78, 85)])

    con = _db(os.path.join(dbd, "bank_signal"))
    con.execute("CREATE TABLE SignalEventDb(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " priority INTEGER, deviceDateTime INTEGER, signalEvent TEXT)")
    coords = {n: (la, lo) for n, la, lo in PLACES}
    for name, day in sorted(TRACK, key=lambda x: x[1]):
        la, lo = coords[name]
        # a few metres of GPS jitter, as a real fix would have
        la += rnd.uniform(-0.0008, 0.0008)
        lo += rnd.uniform(-0.0008, 0.0008)
        con.execute("INSERT INTO SignalEventDb(priority,deviceDateTime,signalEvent) VALUES(0,?,?)",
                    (T0 + day * MS_DAY + rnd.randint(0, 20) * 3_600_000,
                     json.dumps({"eventType": "location_event",
                                 "payload": json.dumps({"latitude": round(la, 6),
                                                        "longitude": round(lo, 6),
                                                        "speed": round(rnd.uniform(0, 14), 1)})})))
    con.commit(); con.close()

    con = _db(os.path.join(dbd, "cache_database"))
    con.execute("CREATE TABLE cache_table(vpa TEXT PRIMARY KEY, name TEXT, verified_name TEXT,"
                " mid TEXT, mcc TEXT, last_updated TEXT, is_verified_merchant INTEGER)")
    for name, vpa, *_ in MERCHANTS:
        con.execute("INSERT INTO cache_table VALUES(?,?,?,?,?,?,1)",
                    (vpa, name, name, f"MID{abs(hash(vpa)) % 10**6}", "5812", "1"))
    con.commit(); con.close()

    con = _db(os.path.join(dbd, "search_db"))
    con.execute("CREATE TABLE recent_Search_Tbl(id TEXT PRIMARY KEY, vertical_id TEXT,"
                " item TEXT, timestamp INTEGER)")
    for q in ("electricity bill", "movie tickets", "mobile recharge"):
        con.execute("INSERT INTO recent_Search_Tbl VALUES(?,?,?,?)",
                    (q, "1", json.dumps({"cta": {"label": q}}), T0 + 20 * MS_DAY))
    con.commit(); con.close()

    con = _db(os.path.join(dbd, "PaytmMessageDatabase"))
    con.execute("CREATE TABLE NotificationData(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,"
                " message TEXT, deep_link TEXT, campaignId TEXT, pushId TEXT, receiveTime INTEGER)")
    for i, (ti, ms) in enumerate((("Payment successful", "You paid Acme Foods Pvt Ltd"),
                                  ("Cashback credited", "₹25 cashback added"),
                                  ("Reminder", "Electricity bill due"))):
        con.execute("INSERT INTO NotificationData(title,message,deep_link,campaignId,pushId,"
                    "receiveTime) VALUES(?,?,?,?,?,?)",
                    (ti, ms, "paytmmp://demo", f"CAMP{i}", f"PUSH{i}", T0 + (25 + i) * MS_DAY))
    con.commit(); con.close()

    con = _db(os.path.join(dbd, "appManagerDB"))
    con.execute("CREATE TABLE ItemTable(keyValue TEXT PRIMARY KEY, value TEXT)")
    for k, v in (("AutoReadOtpEnable", "true"), ("MaxInstrumentCacheLimitAndroid", "25"),
                 ("walletLandingGenericURL_android", "https://demo.example/wallet"),
                 ("GoldP2PMaxAmount", "50000"), ("AddMoneyRefundOption", "source"),
                 ("vkycinternalH5Url", "https://demo.example/vkyc")):
        con.execute("INSERT INTO ItemTable VALUES(?,?)", (k, v))
    con.commit(); con.close()

    con = _db(os.path.join(dbd, "paytmbank_error_analytics"))
    con.execute("CREATE TABLE PBHawkEyeEvent(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " event_type TEXT, customer_id TEXT, device_id TEXT, network_type TEXT,"
                " event_data TEXT, event_log_time INTEGER)")
    for i in range(12):
        con.execute("INSERT INTO PBHawkEyeEvent(event_type,customer_id,device_id,network_type,"
                    "event_data,event_log_time) VALUES(?,?,?,?,?,?)",
                    ("apiLog", SUBJECT["cid"], "demo-device-0001", "4G",
                     json.dumps({"appVersion": "10.0.0", "customMessage": "demo diagnostic",
                                 "flowName": "passbook", "screenName": "PassbookActivity",
                                 "batteryPercentage": 50 + i,
                                 # telemetry stamps one CACHED coordinate on every event —
                                 # exactly the pattern the distinct-position count exposes
                                 "location": {"lat": 12.9352, "lon": 77.6245}}),
                     T0 + (12 + i) * MS_DAY))
    con.commit(); con.close()

    con = _db(os.path.join(nb, "androidx.work.workdb"))
    con.execute("CREATE TABLE WorkSpec(id TEXT PRIMARY KEY, state INTEGER,"
                " worker_class_name TEXT, last_enqueue_time INTEGER, interval_duration INTEGER,"
                " run_attempt_count INTEGER, period_count INTEGER)")
    con.execute("CREATE TABLE WorkName(name TEXT, work_spec_id TEXT)")
    con.execute("CREATE TABLE WorkTag(tag TEXT, work_spec_id TEXT)")
    for i, (cls, nm) in enumerate((("net.one97.paytm.smssdk.workman.SmsProcessWorker", "sms-sync"),
                                   ("net.one97.paytm.contacts.ContactsSyncWorker", "contact-sync"),
                                   ("net.one97.paytm.signal.SignalUploadWorker", "signal-upload"))):
        wid = f"work-{i}"
        con.execute("INSERT INTO WorkSpec VALUES(?,?,?,?,?,?,?)",
                    (wid, i % 3, cls, T0 + (30 + i) * MS_DAY, 900000, i, 1))
        con.execute("INSERT INTO WorkName VALUES(?,?)", (nm, wid))
        con.execute("INSERT INTO WorkTag VALUES(?,?)", (f"periodic:{nm}", wid))
    con.commit(); con.close()

    # ---- prefs, encrypted stores, capabilities ------------------------------ #
    with open(os.path.join(sp, "bank_secure_prefs.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<map>\n'
                f'<string name="userId">{SUBJECT["cid"]}</string>\n'
                f'<string name="mobile">{SUBJECT["phone"]}</string>\n'
                '<string name="kyc_state">PAYTM_PRIMITIVE</string>\n'
                '<string name="ppb_bank_type">DEMO</string>\n'
                '<string name="sso_token=">DEMO-TOKEN-SHOULD-BE-REDACTED-0123456789</string>\n'
                '</map>\n')
    for name in ("Data.xml", "DataUPI.xml", "DataERUPEE.xml"):
        with open(os.path.join(sp, name), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?>\n<map>\n'
                    '<string name="k0">ZmFrZS1jaXBoZXJ0ZXh0LWZvci1kZW1v</string>\n</map>\n')
    for name in ("TPAP_UPI.json", "HOME.json", "CHAT_feed.json"):
        with open(os.path.join(root, "shared_jsons", name), "w", encoding="utf-8") as f:
            f.write("ZmFrZWNpcGhlcnRleHQ" + "A" * 200 + "==")
    with open(os.path.join(root, "app_uthDir", "uthSearchFilterMetaData1.json"), "w",
              encoding="utf-8") as f:
        json.dump({"apiUrlsInfo": {"passbookList": "https://demo.example/api/passbook"},
                   "filters": [{"displayName": "Payment mode", "requestParam": "paymentSystem",
                                "values": [{"displayName": "UPI", "isActive": True,
                                            "requestParamValue": "UPI"},
                                           {"displayName": "Wallet", "isActive": True,
                                            "requestParamValue": "WALLET"}]}]}, f)
    shutil.rmtree(live, ignore_errors=True)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = os.path.abspath(sys.argv[1])
    root = os.path.join(out, "net.one97.paytm")
    case = os.path.join(out, "case")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(root, exist_ok=True)

    print(f"[*] building fabricated extraction -> {root}")
    build_extraction(root)

    from paytmforensics.core.case import Case
    c = Case(root, case, case_id="DEMO-2026-001", examiner="A. Examiner",
             evidence_number="DEMO-EVIDENCE-1",
             notes="Fabricated demonstration data. No real device or person.")
    c.ingest()
    res = c.parse_all()
    n_carved = c.carve()
    c.correlate()
    c.build_timeline()
    c.close()

    from paytmforensics.report import html as hr
    hr.generate(case, os.path.join(case, "report.html"), fmt="html")

    print(f"[+] extraction : {root}")
    print(f"[+] case       : {case}")
    print(f"[+] parsers    : {sum(v for v in res.values() if v and v > 0)} records, "
          f"{n_carved} carved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
