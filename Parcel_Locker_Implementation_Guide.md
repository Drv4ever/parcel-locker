# Implementation Guide — Cloud-Based Digital Locker & Parcel Notification System

This is a build-in-order guide. Follow the phases top to bottom — each one produces something testable before you move to the next, so you always have a working (if incomplete) system rather than a pile of disconnected services.

**Suggested split of work** (matches your existing role split):
- **Abhishek** — Phases 1, 2, 4, 5, 6, 8 (IAM, DynamoDB, SNS/SQS, Lambda backend, EventBridge, CloudWatch)
- **Dhruv** — Phases 3, 7, 9 (EC2 dashboard, S3 integration, testing/demo)

---

## Phase 0 — Prerequisites

- AWS account with a non-root IAM admin user (never build with the root account)
- AWS CLI v2 installed and configured (`aws configure`) — makes everything below scriptable and demo-repeatable
- Pick **one region** and use it everywhere (e.g. `ap-south-1` for Mumbai — lowest latency from VIT)
- Node.js 18+ on your local machine (for Lambda packaging) and on the EC2 instance (for the dashboard)

```bash
aws configure
# AWS Access Key ID, Secret, region (e.g. ap-south-1), output format: json
```

---

## Phase 1 — IAM (do this first, everything else references these roles)

Create three roles with least-privilege policies. Do this in the console (IAM → Roles → Create role) or CLI — console is easier to demo in a viva screenshot.

### 1a. Lambda execution role — `parcel-lambda-role`
Trust policy: AWS service → Lambda. Attach:
- `AWSLambdaBasicExecutionRole` (managed, for CloudWatch Logs)
- Custom inline policy `parcel-lambda-policy`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:Query","dynamodb:Scan"],
      "Resource": "arn:aws:dynamodb:REGION:ACCOUNT_ID:table/ParcelTable"
    },
    {
      "Effect": "Allow",
      "Action": ["sns:Publish"],
      "Resource": "arn:aws:sns:REGION:ACCOUNT_ID:parcel-events"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject","s3:GetObject"],
      "Resource": ["arn:aws:s3:::parcel-photos-*/*", "arn:aws:s3:::parcel-audit-logs-*/*"]
    },
    {
      "Effect": "Allow",
      "Action": ["sqs:SendMessage"],
      "Resource": "arn:aws:sqs:REGION:ACCOUNT_ID:parcel-dlq"
    },
    {
      "Effect": "Allow",
      "Action": ["cloudwatch:PutMetricData"],
      "Resource": "*"
    }
  ]
}
```

### 1b. EC2 instance role — `parcel-ec2-role`
Trust policy: EC2. Attach inline policy allowing:
- `lambda:InvokeFunction` on the Core Logic Lambda's ARN
- `s3:PutObject`/`s3:GetObject` on the photos bucket
- `dynamodb:GetItem`/`Query` on ParcelTable (for the dashboard's read-only status view)

### 1c. Student notify role
Reuse `parcel-lambda-role` for all Lambdas unless your professor wants role-per-function granularity — if so, clone it and trim the actions each function doesn't need (e.g. the Audit Lambda only needs `s3:PutObject`, not DynamoDB write).

**Talking point for viva:** this is where you demonstrate "least privilege" from Module 1 — each function's role only grants what that function actually touches.

---

## Phase 2 — DynamoDB (source of truth)

### Table: `ParcelTable`

| Attribute | Type | Role |
|---|---|---|
| `parcelId` | String (UUID) | Partition key |
| `studentId` | String | For querying "all parcels for a student" |
| `status` | String | `pending_notification` \| `notified` \| `picked_up` \| `reminder_sent` \| `escalated` \| `FAILED_TO_NOTIFY` |
| `courierName` | String | Metadata |
| `photoUrl` | String | S3 object URL |
| `createdAt` | Number (epoch ms) | Used by the State Auditor to compute "stuck > X" |
| `lastUpdatedAt` | Number (epoch ms) | Same |
| `qrCode` | String | Unique pickup token |

Add a **Global Secondary Index** `status-index` (partition key: `status`) — this is what lets the State Auditor Lambda cheaply query "all parcels currently in `notified`" instead of scanning the whole table.

```bash
aws dynamodb create-table \
  --table-name ParcelTable \
  --attribute-definitions \
      AttributeName=parcelId,AttributeType=S \
      AttributeName=status,AttributeType=S \
  --key-schema AttributeName=parcelId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --global-secondary-indexes '[{
      "IndexName": "status-index",
      "KeySchema": [{"AttributeName":"status","KeyType":"HASH"}],
      "Projection": {"ProjectionType":"ALL"}
  }]'
```

`PAY_PER_REQUEST` billing avoids provisioning guesswork for a demo-scale project.

---

## Phase 3 — S3 buckets

```bash
aws s3 mb s3://parcel-photos-<yourname>-demo
aws s3 mb s3://parcel-audit-logs-<yourname>-demo
```

On the photos bucket, add a lifecycle rule (S3 console → Management → Lifecycle rules) to transition objects to Glacier or expire after 90 days — this is your "lifecycle policy" talking point for Module 2. Block all public access on both buckets; the dashboard reads photos via pre-signed URLs, not public links.

---

## Phase 4 — SNS + SQS (the fanout)

```bash
# Topic
aws sns create-topic --name parcel-events

# DLQ
aws sqs create-queue --queue-name parcel-dlq

# Subscribe the DLQ to catch anything SNS itself can't deliver (redrive policy goes on each Lambda's event source / SNS subscription later)
```

Each Lambda subscriber (Student Notify, Analytics, Audit) gets its own SNS subscription to the `parcel-events` topic, filtered if needed (e.g. Analytics might want every event; Student Notify only wants `status = notified`). Use **SNS subscription filter policies** so one topic can serve all three without each Lambda re-filtering in code:

```json
{ "eventType": ["parcel_notified"] }
```

For the DLQ wiring: set each Lambda's **on-failure destination** (Lambda console → Configuration → Asynchronous invocation → Failure destination) to the SQS DLQ ARN. This is what makes "failure → SQS → CloudWatch Alarm" real rather than assumed.

---

## Phase 5 — Lambda functions (the core logic)

Use Python 3.12 runtime — shortest code for this use case. Package each function separately.

### 5a. Core Logic Lambda (`parcel-core-logic`)
Triggered by the EC2 dashboard (via API call or direct Lambda invoke) when a warden logs a parcel or scans a pickup QR.

```python
import boto3, uuid, time, json, os

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('ParcelTable')
sns = boto3.client('sns')
TOPIC_ARN = os.environ['TOPIC_ARN']

def lambda_handler(event, context):
    action = event.get('action')  # "log_parcel" or "scan_pickup"
    now = int(time.time() * 1000)

    if action == 'log_parcel':
        parcel_id = str(uuid.uuid4())
        item = {
            'parcelId': parcel_id,
            'studentId': event['studentId'],
            'status': 'pending_notification',
            'courierName': event.get('courierName', 'Unknown'),
            'photoUrl': event.get('photoUrl', ''),
            'createdAt': now,
            'lastUpdatedAt': now,
            'qrCode': str(uuid.uuid4())[:8]
        }
        table.put_item(Item=item)

        # Publish to SNS fanout — Student Notify, Analytics, Audit all react independently
        sns.publish(
            TopicArn=TOPIC_ARN,
            Message=json.dumps(item),
            MessageAttributes={
                'eventType': {'DataType': 'String', 'StringValue': 'parcel_logged'}
            }
        )
        # If SNS publish succeeds, move to "notified"; a real system would
        # confirm delivery via the Student Notify Lambda's own status update
        table.update_item(
            Key={'parcelId': parcel_id},
            UpdateExpression='SET #s = :s, lastUpdatedAt = :t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'notified', ':t': now}
        )
        return {'statusCode': 200, 'body': json.dumps({'parcelId': parcel_id})}

    elif action == 'scan_pickup':
        parcel_id = event['parcelId']
        table.update_item(
            Key={'parcelId': parcel_id},
            UpdateExpression='SET #s = :s, lastUpdatedAt = :t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'picked_up', ':t': now}
        )
        return {'statusCode': 200, 'body': json.dumps({'status': 'picked_up'})}

    return {'statusCode': 400, 'body': 'Unknown action'}
```

Environment variable: `TOPIC_ARN` = your `parcel-events` topic ARN.

### 5b. Student Notify Lambda (`parcel-student-notify`)
Subscribed to SNS. Uses **SNS itself** (email/SMS subscription) or SES to actually message the student — for a demo, printing/logging + an SES email is enough.

```python
import json, boto3

ses = boto3.client('ses')

def lambda_handler(event, context):
    for record in event['Records']:
        msg = json.loads(record['Sns']['Message'])
        student_id = msg['studentId']
        # Demo: send an email via SES (must verify sender/recipient in SES sandbox)
        ses.send_email(
            Source='your-verified-sender@example.com',
            Destination={'ToAddresses': [f'{student_id}@vitstudent.ac.in']},
            Message={
                'Subject': {'Data': 'Parcel Arrived'},
                'Body': {'Text': {'Data': f"Your parcel from {msg['courierName']} has arrived. Pickup code: {msg['qrCode']}"}}
            }
        )
    return {'statusCode': 200}
```

### 5c. Analytics Lambda (`parcel-analytics`)
```python
import json, boto3

cloudwatch = boto3.client('cloudwatch')

def lambda_handler(event, context):
    for record in event['Records']:
        msg = json.loads(record['Sns']['Message'])
        cloudwatch.put_metric_data(
            Namespace='ParcelSystem',
            MetricData=[{
                'MetricName': 'ParcelsLogged',
                'Value': 1,
                'Unit': 'Count'
            }]
        )
    return {'statusCode': 200}
```

### 5d. Audit Lambda (`parcel-audit`)
```python
import json, boto3, time

s3 = boto3.client('s3')
BUCKET = 'parcel-audit-logs-<yourname>-demo'

def lambda_handler(event, context):
    for record in event['Records']:
        msg = json.loads(record['Sns']['Message'])
        key = f"audit/{msg['parcelId']}-{int(time.time())}.json"
        s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(msg))
    return {'statusCode': 200}
```

### 5e. State Auditor Lambda (`parcel-state-auditor`)
Triggered by EventBridge on a schedule (Phase 6). Scans the `status-index` GSI for stuck parcels.

```python
import boto3, time

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('ParcelTable')

TEN_MIN = 10 * 60 * 1000
FORTY_EIGHT_HR = 48 * 60 * 60 * 1000
FIVE_DAYS = 5 * 24 * 60 * 60 * 1000

def lambda_handler(event, context):
    now = int(time.time() * 1000)

    # Check pending_notification stuck > 10 min
    resp = table.query(
        IndexName='status-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('pending_notification')
    )
    for item in resp['Items']:
        if now - item['createdAt'] > TEN_MIN:
            _update(item['parcelId'], 'FAILED_TO_NOTIFY', now)

    # Check notified stuck > 48 hr
    resp = table.query(
        IndexName='status-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('notified')
    )
    for item in resp['Items']:
        if now - item['lastUpdatedAt'] > FORTY_EIGHT_HR:
            _update(item['parcelId'], 'reminder_sent', now)

    # Check reminder_sent stuck > 5 days
    resp = table.query(
        IndexName='status-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('reminder_sent')
    )
    for item in resp['Items']:
        if now - item['lastUpdatedAt'] > FIVE_DAYS:
            _update(item['parcelId'], 'escalated', now)

    return {'statusCode': 200}

def _update(parcel_id, new_status, now):
    table.update_item(
        Key={'parcelId': parcel_id},
        UpdateExpression='SET #s = :s, lastUpdatedAt = :t',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': new_status, ':t': now}
    )
```

### Packaging & deploying each function
```bash
zip function.zip lambda_function.py
aws lambda create-function \
  --function-name parcel-core-logic \
  --runtime python3.12 \
  --role arn:aws:iam::ACCOUNT_ID:role/parcel-lambda-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://function.zip \
  --environment Variables={TOPIC_ARN=arn:aws:sns:REGION:ACCOUNT_ID:parcel-events}
```
Repeat for each function, then subscribe the appropriate ones to SNS:
```bash
aws sns subscribe --topic-arn arn:aws:sns:REGION:ACCOUNT_ID:parcel-events \
  --protocol lambda --notification-endpoint arn:aws:lambda:REGION:ACCOUNT_ID:function:parcel-student-notify

aws lambda add-permission --function-name parcel-student-notify \
  --statement-id sns-invoke --action lambda:InvokeFunction \
  --principal sns.amazonaws.com --source-arn arn:aws:sns:REGION:ACCOUNT_ID:parcel-events
```
(Same pair of commands for `parcel-analytics` and `parcel-audit`.)

---

## Phase 6 — EventBridge scheduled rule

```bash
aws events put-rule \
  --name parcel-state-audit-schedule \
  --schedule-expression "rate(15 minutes)"

aws lambda add-permission --function-name parcel-state-auditor \
  --statement-id eventbridge-invoke --action lambda:InvokeFunction \
  --principal events.amazonaws.com --source-arn arn:aws:events:REGION:ACCOUNT_ID:rule/parcel-state-audit-schedule

aws events put-targets --rule parcel-state-audit-schedule \
  --targets "Id"="1","Arn"="arn:aws:lambda:REGION:ACCOUNT_ID:function:parcel-state-auditor"
```

---

## Phase 7 — EC2 warden dashboard

### 7a. Networking
- Create a VPC (or use default), one public subnet
- Security group `parcel-dashboard-sg`: inbound 22 (SSH, your IP only), 80/443 (HTTP/S, or a custom port like 3000 for a demo), outbound all

### 7b. Launch instance
- Amazon Linux 2023, t2.micro (free tier)
- Attach the `parcel-ec2-role` IAM role from Phase 1b
- Install Node.js: `sudo dnf install -y nodejs`

### 7c. Minimal dashboard app (Node/Express)
A single-file app is enough for a viva demo: a form to log a parcel (photo upload → S3, then invoke the Core Logic Lambda), a QR scan input (paste/type the code, invoke `scan_pickup`), and a status table (read from DynamoDB).

```javascript
// server.js
const express = require('express');
const multer = require('multer');
const AWS = require('aws-sdk');
const app = express();
const upload = multer({ storage: multer.memoryStorage() });

AWS.config.update({ region: 'REGION' });
const s3 = new AWS.S3();
const lambda = new AWS.Lambda();
const dynamodb = new AWS.DynamoDB.DocumentClient();

app.use(express.urlencoded({ extended: true }));
app.use(express.static('public'));

app.post('/log-parcel', upload.single('photo'), async (req, res) => {
  const key = `photos/${Date.now()}-${req.file.originalname}`;
  await s3.putObject({
    Bucket: 'parcel-photos-<yourname>-demo',
    Key: key,
    Body: req.file.buffer,
    ContentType: req.file.mimetype
  }).promise();

  const photoUrl = `https://parcel-photos-<yourname>-demo.s3.amazonaws.com/${key}`;

  const result = await lambda.invoke({
    FunctionName: 'parcel-core-logic',
    Payload: JSON.stringify({
      action: 'log_parcel',
      studentId: req.body.studentId,
      courierName: req.body.courierName,
      photoUrl
    })
  }).promise();

  res.redirect('/?logged=1');
});

app.post('/scan-pickup', async (req, res) => {
  await lambda.invoke({
    FunctionName: 'parcel-core-logic',
    Payload: JSON.stringify({ action: 'scan_pickup', parcelId: req.body.parcelId })
  }).promise();
  res.redirect('/?picked=1');
});

app.get('/status', async (req, res) => {
  const result = await dynamodb.scan({ TableName: 'ParcelTable' }).promise();
  res.json(result.Items);
});

app.listen(3000, () => console.log('Dashboard running on port 3000'));
```

```bash
npm init -y
npm install express multer aws-sdk
node server.js
```

A minimal `public/index.html` with two `<form>`s (photo+studentId+courierName → `/log-parcel`, parcelId → `/scan-pickup`) and a `fetch('/status')` table is enough — keep it simple, the AWS wiring is what's being graded, not the frontend polish.

---

## Phase 8 — CloudWatch

- **Dashboard**: CloudWatch → Dashboards → add a widget on the `ParcelSystem` / `ParcelsLogged` custom metric (from the Analytics Lambda)
- **Alarm**: CloudWatch → Alarms → create alarm on `ApproximateNumberOfMessagesVisible` for the `parcel-dlq` SQS queue, threshold ≥ 1 → SNS topic (email you) — this is your "no manual monitoring needed" proof for the viva

---

## Phase 9 — End-to-end test

1. Open the dashboard, submit a parcel with a photo → confirm it appears in `ParcelTable` with `status = notified`
2. Check S3 audit bucket for the JSON audit record
3. Check CloudWatch metric `ParcelsLogged` incremented
4. Manually set a test item's `createdAt` to 20 minutes ago via the DynamoDB console, wait for the next EventBridge tick (or manually invoke `parcel-state-auditor`) → confirm it flips to `FAILED_TO_NOTIFY`
5. Break something on purpose (e.g. temporarily remove the Audit Lambda's S3 permission) → confirm the message lands in the DLQ and the alarm fires — this single test demonstrates modules 1, 4, and 7 together and is worth rehearsing for the viva

---

## Cost & cleanup note
Everything above fits comfortably in the AWS Free Tier for a few weeks of dev/demo use (Lambda, DynamoDB on-demand, SNS/SQS, and t2.micro EC2 all have free tiers). After your viva, tear down in reverse order — EC2 instance → Lambda functions → EventBridge rule → SNS/SQS → DynamoDB table → S3 buckets (empty first) — so nothing keeps billing.

---

## Suggested build order if you're short on time
If the viva is close, prioritize: **Phase 1 → 2 → 5a → 7 (dashboard talking to Core Logic + DynamoDB only)** first — that alone is a demoable "warden logs parcel, status tracked" system. Then layer in SNS fanout (5b–5d), then EventBridge auditing (5e + 6), then CloudWatch (8) last, since it's the easiest to explain conceptually without a live demo if you run out of time.
