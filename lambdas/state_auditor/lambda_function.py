import boto3
import time
import os

dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'ap-south-1'))
table = dynamodb.Table('ParcelTable')

TEN_MIN     = 10 * 60 * 1000          # 10 minutes in milliseconds
FORTY_EIGHT_HR = 48 * 60 * 60 * 1000  # 48 hours in milliseconds
FIVE_DAYS   = 5 * 24 * 60 * 60 * 1000 # 5 days in milliseconds


def _query_all(status):
    """
    Paginated GSI query — fetches ALL items with the given status,
    even when the result set exceeds DynamoDB's 1 MB per-page limit.
    """
    items = []
    kwargs = {
        'IndexName': 'status-index',
        'KeyConditionExpression': boto3.dynamodb.conditions.Key('status').eq(status),
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get('Items', []))
        last_key = resp.get('LastEvaluatedKey')
        if not last_key:
            break
        kwargs['ExclusiveStartKey'] = last_key
    return items


def _update(parcel_id, new_status, now):
    table.update_item(
        Key={'parcelId': parcel_id},
        UpdateExpression='SET #s = :s, lastUpdatedAt = :t',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': new_status, ':t': now},
    )


def lambda_handler(event, context):
    now = int(time.time() * 1000)
    print(f"Starting scheduled audit scan at epoch {now}")

    # ── Rule 1: pending_notification stuck > 10 min → FAILED_TO_NOTIFY ──────
    # This catches parcels where SNS publish succeeded but the Student Notify
    # Lambda never updated the status back (e.g. SES hard-bounce, cold-start
    # timeout). In the current core_logic the status is set to 'notified'
    # optimistically right after sns.publish() — so a parcel should almost
    # never sit here unless the core_logic Lambda itself crashed mid-flight.
    for item in _query_all('pending_notification'):
        if now - item.get('createdAt', now) > TEN_MIN:
            print(f"Flagging {item['parcelId']} as FAILED_TO_NOTIFY (stuck {now - item['createdAt']} ms)")
            _update(item['parcelId'], 'FAILED_TO_NOTIFY', now)

    # ── Rule 2: notified stuck > 48 hr → reminder_sent ──────────────────────
    for item in _query_all('notified'):
        age = now - item.get('lastUpdatedAt', now)
        if age > FORTY_EIGHT_HR:
            print(f"Sending reminder for {item['parcelId']} (notified {age} ms ago)")
            _update(item['parcelId'], 'reminder_sent', now)

    # ── Rule 3: reminder_sent stuck > 5 days → escalated ────────────────────
    for item in _query_all('reminder_sent'):
        age = now - item.get('lastUpdatedAt', now)
        if age > FIVE_DAYS:
            print(f"Escalating {item['parcelId']} (reminder_sent {age} ms ago)")
            _update(item['parcelId'], 'escalated', now)

    print("Audit scan complete.")
    return {'statusCode': 200, 'message': 'Audit completed successfully'}