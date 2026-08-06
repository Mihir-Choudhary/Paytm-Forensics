"""Builds a synthetic, ground-truth Paytm-shaped extraction for validation/CI.

Lets the full pipeline be tested WITHOUT the private real extraction. Schemas mirror the
real DBs closely enough for the column-name-based parsers; values are fabricated and known.
"""
from __future__ import annotations

import json
import os
import sqlite3


def _db(path, wal: bool = False):
    con = sqlite3.connect(path)
    con.execute("PRAGMA auto_vacuum=NONE")
    con.execute("PRAGMA secure_delete=OFF")
    if wal:
        # F-02: no fixture previously used WAL mode, so no test could exercise the
        # uncheckpointed-rows failure at all.
        con.execute("PRAGMA journal_mode=WAL")
    return con


def build(root: str) -> dict:
    """Create a synthetic extraction under `root`. Returns the expected ground truth."""
    dbd = os.path.join(root, "databases")
    sp = os.path.join(root, "shared_prefs")
    nb = os.path.join(root, "no_backup")
    for d in (dbd, sp, nb):
        os.makedirs(d, exist_ok=True)

    # ---- passbook.db (2 transactions) ---- #
    con = _db(os.path.join(dbd, "passbook.db"))
    con.execute("""CREATE TABLE UthListingEntity(
        txnId TEXT, sourceTxnId TEXT PRIMARY KEY, amount REAL, txnIndicator INTEGER,
        identifier TEXT, secondPartyInfo TEXT, statusKey INTEGER, narration TEXT,
        streamSource TEXT, txnCategory INTEGER, txnDate INTEGER, txnTag TEXT,
        userInstrumentInfo TEXT, errorCode TEXT)""")
    con.execute("CREATE TABLE UthInstrumentEntity(sourceTxnId TEXT, instrumentName TEXT, id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO UthListingEntity VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("T1", "PTMAAA111", 76.0, 2, "merchant@ptybl",
                 json.dumps({"name": "TestMerchant"}), 2, "Order 1", "UPI", 1,
                 1723742756754, "Food", json.dumps([{"instrumentName": "HDFC Bank"}]), None))
    con.execute("INSERT INTO UthListingEntity VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("T2", "PTMBBB222", 50.0, 1, "cashback@axisbank",
                 json.dumps({"name": "Cashback"}), 2, None, "UPI", 2,
                 1723842756754, "Cashback", "[]", "1103"))
    con.execute("INSERT INTO UthInstrumentEntity VALUES('PTMAAA111','HDFC Bank',1)")
    con.commit(); con.close()

    # ---- chatDb.db (1 me + 1 other; 2 messages with rrn) ---- #
    con = _db(os.path.join(dbd, "chatDb.db"))
    con.execute("""CREATE TABLE TBL_USERS(
        isMe INTEGER, sendbirdUserName TEXT, sendbirdUserId TEXT, phoneNumber TEXT,
        identifier TEXT, type TEXT, vpa TEXT, name TEXT, bankName TEXT, maskedAccNo TEXT,
        verifiedName TEXT, getInfoSync TEXT, getPaymentInfoSync TEXT, userPrimaryKey INTEGER PRIMARY KEY)""")
    con.execute("INSERT INTO TBL_USERS VALUES(1,'Me User','SB-ME','9999900000','CID-ME','CUSTOMER',NULL,'Me User',NULL,NULL,NULL,NULL,NULL,1)")
    age = json.dumps({"jsonString": json.dumps({"customerCreationText": "On Paytm Since Jan 2020"})})
    con.execute("INSERT INTO TBL_USERS VALUES(0,'Other Person','SB-OTH','8888811111','CID-OTH','CUSTOMER',NULL,'Other Person',NULL,NULL,NULL,?,?,2)",
                (age, age))
    con.execute("""CREATE TABLE ChatMessageEntity(
        id INTEGER PRIMARY KEY, data TEXT, messageContent TEXT, senderName TEXT,
        senderId TEXT, createdAt INTEGER, customType TEXT, channelUrl TEXT,
        messageState INTEGER, messageType TEXT, rawMessage BLOB)""")
    con.execute("INSERT INTO ChatMessageEntity VALUES(1,?,?,?,?,?,?,?,?,?,?)",
                (json.dumps({"amount": "20", "rrn": "100000000001", "msgStatus": "SUCCESS",
                             "paymentMode": "UPI", "uniqueKey": "U1"}),
                 "Sent you ₹20", "Other Person", "SB-OTH", 1723742756754,
                 "TRANSFER", "chan1", 6, "TRANSFER", b"\x01\x02"))
    con.execute("INSERT INTO ChatMessageEntity VALUES(2,?,?,?,?,?,?,?,?,?,?)",
                (json.dumps({"amount": "45", "rrn": "100000000002", "msgStatus": "SUCCESS",
                             "paymentMode": "UPI", "uniqueKey": "U2"}),
                 "Sent you ₹45", "Other Person", "SB-OTH", 1723842756754,
                 "TRANSFER", "chan1", 6, "TRANSFER", None))
    con.commit(); con.close()

    # ---- ups_database (2 consents) ---- #
    con = _db(os.path.join(dbd, "ups_database"))
    con.execute("CREATE TABLE ConsentTable(consentKey TEXT PRIMARY KEY, consentValue TEXT, syncedWithServer INTEGER, verticalId TEXT, syncTimestamp INTEGER)")
    con.execute("INSERT INTO ConsentTable VALUES('ocl.permission.universal.sms_read_consent','true',1,'-1',1724056313970)")
    con.execute("INSERT INTO ConsentTable VALUES('ocl.permission.contact_management.contact_sync_consent','true',1,'-1',1724056313970)")
    con.commit(); con.close()

    # ---- bank_signal (1 location) ---- #
    con = _db(os.path.join(dbd, "bank_signal"))
    con.execute("CREATE TABLE SignalEventDb(id INTEGER PRIMARY KEY, priority INTEGER, deviceDateTime INTEGER, signalEvent TEXT)")
    con.execute("INSERT INTO SignalEventDb VALUES(1,0,1724056316881,?)",
                (json.dumps({"eventType": "location_event",
                             "payload": json.dumps({"latitude": 1.2345, "longitude": 6.7890, "speed": 0.0})}),))
    con.commit(); con.close()

    # ---- cache_database (1 vpa) ---- #
    con = _db(os.path.join(dbd, "cache_database"))
    con.execute("CREATE TABLE cache_table(vpa TEXT PRIMARY KEY, name TEXT, verified_name TEXT, mid TEXT, mcc TEXT, last_updated TEXT, is_verified_merchant INTEGER)")
    con.execute("INSERT INTO cache_table VALUES('merchant@ptybl','TestMerchant','TestMerchant','','5812','1',1)")
    con.commit(); con.close()

    # ---- workdb (1 job) ---- #
    con = _db(os.path.join(nb, "androidx.work.workdb"))
    con.execute("CREATE TABLE WorkSpec(id TEXT PRIMARY KEY, state INTEGER, worker_class_name TEXT, last_enqueue_time INTEGER, interval_duration INTEGER, run_attempt_count INTEGER)")
    con.execute("INSERT INTO WorkSpec VALUES('w1',0,'net.one97.paytm.smssdk.workman.SmsProcessWorker',1724056316881,0,0)")
    con.commit(); con.close()

    # ---- bank_secure_prefs.xml ---- #
    with open(os.path.join(sp, "bank_secure_prefs.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<map>\n'
                '<string name="userId">CID-ME</string>\n'
                '<string name="mobile">9999900000</string>\n'
                '<string name="kyc_state">PAYTM_PRIMITIVE</string>\n'
                '<string name="sso_token=">SECRET-SHOULD-BE-REDACTED</string>\n</map>\n')

    return {
        "transactions.passbook": 2,
        "chats": 2,
        "contacts.users": 1,        # 2 users - 1 subject
        "contacts.vpa_cache": 1,
        "consents": 2,
        "location.signal": 1,
        "jobs": 1,
        "subject_customer_id": "CID-ME",
        "subject_phone": "9999900000",
        "known_rrns": {"100000000001", "100000000002"},
    }
