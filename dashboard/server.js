const express = require('express');
const multer = require('multer');
const AWS = require('aws-sdk');
const path = require('path');

const app = express();
const upload = multer({ storage: multer.memoryStorage() });

const REGION = process.env.AWS_REGION || 'ap-south-1';
const BUCKET = process.env.PHOTOS_BUCKET || 'parcel-photos-dhruv-652872010155';
const TABLE = process.env.DYNAMO_TABLE || 'ParcelTable';
const LAMBDA_NAME = process.env.CORE_LAMBDA || 'parcel-core-logic';

AWS.config.update({ region: REGION });
const s3 = new AWS.S3();
const lambda = new AWS.Lambda();
const dynamodb = new AWS.DynamoDB.DocumentClient();

app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Log new parcel
app.post('/log-parcel', upload.single('photo'), async (req, res) => {
  try {
    let photoUrl = '';
    if (req.file) {
      const key = `photos/${Date.now()}-${req.file.originalname.replace(/[^a-zA-Z0-9.-]/g, '_')}`;
      await s3.putObject({
        Bucket: BUCKET,
        Key: key,
        Body: req.file.buffer,
        ContentType: req.file.mimetype
      }).promise();
      photoUrl = `https://${BUCKET}.s3.${REGION}.amazonaws.com/${key}`;
    }

    const payload = {
      action: 'log_parcel',
      studentId: req.body.studentId,
      courierName: req.body.courierName,
      photoUrl: photoUrl
    };

    const lambdaResp = await lambda.invoke({
      FunctionName: LAMBDA_NAME,
      Payload: JSON.stringify(payload)
    }).promise();

    const result = JSON.parse(lambdaResp.Payload);
    res.redirect('/?logged=1');
  } catch (err) {
    console.error('Error in /log-parcel:', err);
    res.status(500).send(`Error logging parcel: ${err.message}`);
  }
});

// Scan pickup
app.post('/scan-pickup', async (req, res) => {
  try {
    const parcelId = req.body.parcelId;
    await lambda.invoke({
      FunctionName: LAMBDA_NAME,
      Payload: JSON.stringify({ action: 'scan_pickup', parcelId })
    }).promise();
    res.redirect('/?picked=1');
  } catch (err) {
    console.error('Error in /scan-pickup:', err);
    res.status(500).send(`Error scanning pickup: ${err.message}`);
  }
});

// Get status list
app.get('/status', async (req, res) => {
  try {
    const result = await dynamodb.scan({ TableName: TABLE }).promise();
    // Sort descending by createdAt
    const items = (result.Items || []).sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
    res.json(items);
  } catch (err) {
    console.error('Error fetching status:', err);
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Warden Dashboard listening on http://0.0.0.0:${PORT}`);
});