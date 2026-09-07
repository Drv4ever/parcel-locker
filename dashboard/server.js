const express = require('express');
const multer = require('multer');
const path = require('path');
const crypto = require('crypto');

const { S3Client, PutObjectCommand, GetObjectCommand } = require('@aws-sdk/client-s3');
const { getSignedUrl } = require('@aws-sdk/s3-request-presigner');
const { LambdaClient, InvokeCommand } = require('@aws-sdk/client-lambda');
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, ScanCommand } = require('@aws-sdk/lib-dynamodb');

const app = express();
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 5 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => cb(null, /^image\/(jpeg|png|webp|gif)$/.test(file.mimetype)),
});

const REGION = process.env.AWS_REGION || 'ap-south-1';
const BUCKET = process.env.PHOTOS_BUCKET || 'parcel-photos-dhruv-652872010155';
const TABLE = process.env.DYNAMO_TABLE || 'ParcelTable';
const LAMBDA_NAME = process.env.CORE_LAMBDA || 'parcel-core-logic';
const PORT = Number(process.env.PORT || 3000);

// Credentials must be supplied through the environment. For production, source them
// from Secrets Manager or replace this adapter with Amazon Cognito.
const ADMIN_USERNAME = process.env.ADMIN_USERNAME || '';
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || '';
const STUDENT_PASSWORD = process.env.STUDENT_PASSWORD || '';
const SESSION_SECRET = process.env.SESSION_SECRET || '';
const COOKIE_NAME = 'parcel_session';

if (!ADMIN_USERNAME || !ADMIN_PASSWORD || !STUDENT_PASSWORD || !SESSION_SECRET) {
  throw new Error('ADMIN_USERNAME, ADMIN_PASSWORD, STUDENT_PASSWORD, and SESSION_SECRET must be configured');
}

const s3 = new S3Client({ region: REGION });
const lambdaFn = new LambdaClient({ region: REGION });
const dynamodb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }));

app.use(express.urlencoded({ extended: true }));
app.use(express.json({ limit: '100kb' }));
app.use(express.static(path.join(__dirname, 'public')));

function signSession(user) {
  const body = Buffer.from(JSON.stringify({ ...user, exp: Date.now() + 8 * 60 * 60 * 1000 })).toString('base64url');
  const signature = crypto.createHmac('sha256', SESSION_SECRET).update(body).digest('base64url');
  return `${body}.${signature}`;
}

function readSession(req) {
  const cookie = (req.headers.cookie || '').split(';').map((v) => v.trim()).find((v) => v.startsWith(`${COOKIE_NAME}=`));
  if (!cookie) return null;
  const value = decodeURIComponent(cookie.slice(COOKIE_NAME.length + 1));
  const [body, signature] = value.split('.');
  if (!body || !signature) return null;
  const expected = crypto.createHmac('sha256', SESSION_SECRET).update(body).digest('base64url');
  if (signature.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) return null;
  try {
    const user = JSON.parse(Buffer.from(body, 'base64url').toString());
    return user.exp > Date.now() ? user : null;
  } catch (_error) {
    return null;
  }
}

function setSession(res, user) {
  const secure = process.env.NODE_ENV === 'production' ? '; Secure' : '';
  res.setHeader('Set-Cookie', `${COOKIE_NAME}=${encodeURIComponent(signSession(user))}; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800${secure}`);
}

function clearSession(res) {
  res.setHeader('Set-Cookie', `${COOKIE_NAME}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0`);
}

function requireRole(role) {
  return (req, res, next) => {
    const user = readSession(req);
    if (!user) return res.status(401).json({ error: 'Please sign in first.' });
    if (role && user.role !== role) return res.status(403).json({ error: 'You do not have access to this area.' });
    req.user = user;
    next();
  };
}

async function scanAll(extra = {}) {
  const items = [];
  let lastKey;
  do {
    const response = await dynamodb.send(new ScanCommand({ TableName: TABLE, ...extra, ExclusiveStartKey: lastKey }));
    items.push(...(response.Items || []));
    lastKey = response.LastEvaluatedKey;
  } while (lastKey);
  return items;
}

function photoKeyFromUrl(photoUrl) {
  if (!photoUrl) return null;
  try {
    const pathname = new URL(photoUrl).pathname.replace(/^\//, '');
    return decodeURIComponent(pathname);
  } catch (_error) {
    return null;
  }
}

async function addPrivatePhotoLinks(items) {
  return Promise.all(items.map(async (item) => {
    const key = photoKeyFromUrl(item.photoUrl);
    if (!key) return { ...item, photoUrl: '' };
    try {
      const photoUrl = await getSignedUrl(s3, new GetObjectCommand({ Bucket: BUCKET, Key: key }), { expiresIn: 900 });
      return { ...item, photoUrl };
    } catch (error) {
      console.error(`Unable to sign parcel photo ${key}:`, error);
      return { ...item, photoUrl: '' };
    }
  }));
}

app.get('/api/session', (req, res) => res.json({ user: readSession(req) }));

app.post('/api/login', (req, res) => {
  const role = req.body.role === 'student' ? 'student' : 'admin';
  if (role === 'admin') {
    if (req.body.username !== ADMIN_USERNAME || req.body.password !== ADMIN_PASSWORD) {
      return res.status(401).json({ error: 'Invalid administrator credentials.' });
    }
    setSession(res, { role: 'admin', username: ADMIN_USERNAME });
    return res.json({ role: 'admin', displayName: 'Administrator' });
  }

  const studentId = String(req.body.studentId || '').trim();
  if (!studentId || req.body.password !== STUDENT_PASSWORD) {
    return res.status(401).json({ error: 'Invalid student ID or password.' });
  }
  setSession(res, { role: 'student', studentId, displayName: studentId });
  return res.json({ role: 'student', studentId, displayName: studentId });
});

app.post('/api/logout', (req, res) => {
  clearSession(res);
  res.json({ ok: true });
});

app.get('/api/stats', requireRole('admin'), async (_req, res) => {
  try {
    const items = await scanAll();
    const statuses = items.reduce((result, item) => {
      result[item.status || 'unknown'] = (result[item.status || 'unknown'] || 0) + 1;
      return result;
    }, {});
    res.json({
      total: items.length,
      pending: (statuses.pending_notification || 0) + (statuses.FAILED_TO_NOTIFY || 0),
      notified: statuses.notified || 0,
      pickedUp: statuses.picked_up || 0,
      reminders: (statuses.reminder_sent || 0) + (statuses.escalated || 0),
      statuses,
    });
  } catch (error) {
    console.error('Error loading stats:', error);
    res.status(500).json({ error: 'Unable to load dashboard analytics.' });
  }
});

app.get('/api/parcels', requireRole('admin'), async (_req, res) => {
  try {
    const items = await scanAll();
    items.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
    res.json(await addPrivatePhotoLinks(items));
  } catch (error) {
    console.error('Error loading parcels:', error);
    res.status(500).json({ error: 'Unable to load parcel records.' });
  }
});

app.get('/api/student-status', requireRole('student'), async (req, res) => {
  try {
    const items = await scanAll({
      FilterExpression: 'studentId = :sid',
      ExpressionAttributeValues: { ':sid': req.user.studentId },
    });
    items.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
    res.json(await addPrivatePhotoLinks(items));
  } catch (error) {
    console.error('Error loading student parcels:', error);
    res.status(500).json({ error: 'Unable to load your parcel status.' });
  }
});

app.post('/api/log-parcel', requireRole('admin'), upload.single('photo'), async (req, res) => {
  try {
    const studentId = String(req.body.studentId || '').trim();
    const courierName = String(req.body.courierName || '').trim();
    if (!studentId || !courierName) return res.status(400).json({ error: 'Student ID and courier name are required.' });

    let photoUrl = '';
    if (req.file) {
      const safeName = req.file.originalname.replace(/[^a-zA-Z0-9.-]/g, '_');
      const key = `photos/${Date.now()}-${safeName}`;
      await s3.send(new PutObjectCommand({ Bucket: BUCKET, Key: key, Body: req.file.buffer, ContentType: req.file.mimetype }));
      photoUrl = `https://${BUCKET}.s3.${REGION}.amazonaws.com/${key}`;
    }

    const lambdaResp = await lambdaFn.send(new InvokeCommand({
      FunctionName: LAMBDA_NAME,
      Payload: Buffer.from(JSON.stringify({ action: 'log_parcel', studentId, courierName, photoUrl })),
    }));
    const payload = lambdaResp.Payload ? JSON.parse(Buffer.from(lambdaResp.Payload).toString()) : {};
    if (lambdaResp.FunctionError) return res.status(502).json({ error: 'Parcel service rejected the request.' });
    return res.json({ ok: true, result: payload });
  } catch (error) {
    console.error('Error logging parcel:', error);
    return res.status(500).json({ error: 'Unable to register parcel.' });
  }
});

app.post('/api/scan-pickup', requireRole('admin'), async (req, res) => {
  try {
    const parcelId = String(req.body.parcelId || '').trim();
    const qrCode = String(req.body.qrCode || '').trim();
    if (!parcelId || !qrCode) return res.status(400).json({ error: 'Parcel ID and pickup code are required.' });
    const lambdaResp = await lambdaFn.send(new InvokeCommand({
      FunctionName: LAMBDA_NAME,
      Payload: Buffer.from(JSON.stringify({ action: 'scan_pickup', parcelId, qrCode })),
    }));
    const payload = lambdaResp.Payload ? JSON.parse(Buffer.from(lambdaResp.Payload).toString()) : {};
    if (lambdaResp.FunctionError || payload.statusCode >= 400) return res.status(400).json({ error: 'Pickup code was not accepted.' });
    return res.json({ ok: true });
  } catch (error) {
    console.error('Error completing pickup:', error);
    return res.status(500).json({ error: 'Unable to complete pickup.' });
  }
});

app.listen(PORT, '0.0.0.0', () => console.log(`Parcel Locker dashboard listening on port ${PORT}`));
