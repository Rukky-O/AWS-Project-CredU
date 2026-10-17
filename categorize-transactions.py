import csv, io, hashlib, boto3
from datetime import datetime
from decimal import Decimal
from urllib.parse import unquote_plus

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table("FinanceTracker")

RULES = {
    "food": ["cafe", "pizza", "grocery", "restaurant"],
    "subscriptions": ["spotify", "netflix", "hulu"],
    "transport": ["uber", "lyft", "gas"],
}

def categorize(merchant):
    m = merchant.lower()
    for category, words in RULES.items():
        if any(w in m for w in words):
            return category
    return "other"

def normalize_date(d):
    d = d.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return d

def lambda_handler(event, context):
    record = event["Records"][0]["s3"]
    bucket = record["bucket"]["name"]
    key = unquote_plus(record["object"]["key"])
    user_id = key.split("/")[0]
    pk = f"USER#{user_id}"

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(body))
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]

    count = 0
    for i, row in enumerate(reader):
        date = normalize_date(row["date"])
        amount = Decimal(row["amount"].strip())
        category = categorize(row["description"])
        row_id = hashlib.md5(
            f"{date}|{row['description']}|{row['amount']}|{i}".encode()
        ).hexdigest()[:8]

        old = table.put_item(
            Item={
                "pk": pk,
                "sk": f"TXN#{date}#{row_id}",
                "amount": amount,
                "merchant": row["description"].strip(),
                "category": category,
            },
            ReturnValues="ALL_OLD",
        )

        # Only add to the monthly total if this transaction is new,
        # so re-uploading the same file doesn't double count.
        if "Attributes" not in old and amount < 0:
            table.update_item(
                Key={"pk": pk, "sk": f"SPEND#{date[:7]}#{category}"},
                UpdateExpression="ADD #t :amt",
                ExpressionAttributeNames={"#t": "total"},
                ExpressionAttributeValues={":amt": -amount},
            )
        count += 1

    print("Rows written:", count)
    return {"count": count}