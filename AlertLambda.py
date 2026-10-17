import os, time, boto3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from boto3.dynamodb.conditions import Key

table = boto3.resource("dynamodb").Table("FinanceTracker")
ses = boto3.client("ses")
sns = boto3.client("sns")

SENDER = os.environ["SENDER_EMAIL"]
RECIPIENT = os.environ["RECIPIENT_EMAIL"]
PHONE = os.environ.get("PHONE_NUMBER")  # optional, e.g. +15551234567

def query_all(**kwargs):
    items = []
    while True:
        r = table.query(**kwargs)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return items
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

def save_alert(pk, alert_type, key, message, today):
    table.put_item(Item={
        "pk": pk,
        "sk": f"ALERT#{today}#{alert_type}#{key}",
        "type": alert_type,
        "message": message,
        "read": False,
        "expiresAt": int(time.time()) + 30 * 24 * 3600,  # auto-delete after 30 days
    })

def lambda_handler(event, context):
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    messages = []

    # 1. Bills due in the next 3 days (GSI1)
    for d in range(0, 4):
        due = (now + timedelta(days=d)).strftime("%Y-%m-%d")
        for bill in query_all(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"BILLDUE#{due}"),
        ):
            msg = f"Bill due {due}: {bill.get('name', 'Bill')} (${bill.get('amount')})"
            save_alert(bill["pk"], "bill", bill["sk"], msg, today)
            messages.append(msg)

    # 2. Credit cards above 30% utilization (GSI2)
    for card in query_all(
        IndexName="GSI2",
        KeyConditionExpression=Key("GSI2PK").eq("CREDIT"),
    ):
        limit, balance = card.get("creditLimit"), card.get("balance")
        if limit and balance is not None and limit > 0:
            util = balance / limit
            if util > Decimal("0.30"):
                name = card["sk"].split("#", 1)[1]
                msg = f"Credit utilization on {name} is {round(util * 100)}% (above 30%)."
                save_alert(card["pk"], "utilization", card["sk"], msg, today)
                messages.append(msg)

    # 3. Budgets past their alert % (GSI2 + SPEND totals)
    for b in query_all(
        IndexName="GSI2",
        KeyConditionExpression=Key("GSI2PK").eq("BUDGET"),
    ):
        category = b["sk"].split("#", 1)[1]
        item = table.get_item(
            Key={"pk": b["pk"], "sk": f"SPEND#{month}#{category}"}
        ).get("Item")
        spent = item["total"] if item else Decimal(0)
        limit = b["monthlyLimit"]
        pct = b.get("alertPercent", Decimal(80))
        if spent >= limit * pct / 100:
            msg = f"You've spent ${spent} on {category}, past {pct}% of your ${limit} budget."
            save_alert(b["pk"], "budget", category, msg, today)
            messages.append(msg)

    # Delivery
    if messages:
        text = "\n".join(messages)
        try:
            ses.send_email(
                Source=SENDER,
                Destination={"ToAddresses": [RECIPIENT]},
                Message={
                    "Subject": {"Data": "CredU daily alerts"},
                    "Body": {"Text": {"Data": text}},
                },
            )
        except Exception as e:
            print("Email failed:", e)
        if PHONE:
            try:
                sns.publish(PhoneNumber=PHONE, Message=text[:140])
            except Exception as e:
                print("SMS failed:", e)

    print("Alerts:", messages)
    return {"alerts": len(messages)}