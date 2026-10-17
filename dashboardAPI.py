import json, os, boto3
from decimal import Decimal
from boto3.dynamodb.conditions import Key

table = boto3.resource("dynamodb").Table("FinanceTracker")
cognito = boto3.client("cognito-idp", region_name=os.environ.get("POOL_REGION"))

def clean(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError

def lambda_handler(event, context):
    # 1. Work out which user's data to load
    if os.environ.get("DEMO_MODE") == "true":
        user = os.environ.get("DEMO_USER", "testuser")
    else:
        token = (event.get("headers") or {}).get("authorization", "").replace("Bearer ", "")
        try:
            u = cognito.get_user(AccessToken=token)
        except Exception:
            return {"statusCode": 401, "body": json.dumps({"error": "Not signed in"})}
        email = next((a["Value"] for a in u["UserAttributes"] if a["Name"] == "email"), u["Username"])
        user = os.environ.get("DEMO_USER") or email.split("@")[0].lower()

    # 2. Load that user's items
    kwargs = {"KeyConditionExpression": Key("pk").eq(f"USER#{user}")}
    items = []
    while True:
        r = table.query(**kwargs)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    def pick(prefix):
        return [i for i in items if i["sk"].startswith(prefix)]

    body = {
        "transactions": sorted(pick("TXN#"), key=lambda i: i["sk"], reverse=True)[:50],
        "alerts": sorted(pick("ALERT#"), key=lambda i: i["sk"], reverse=True),
        "accounts": pick("ACCOUNT#"),
        "bills": pick("BILL#"),
        "budgets": pick("BUDGET#"),
        "spend": pick("SPEND#"),
    }
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=clean),
    }