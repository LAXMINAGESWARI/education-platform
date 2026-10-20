import http from "k6/http";
import { check, sleep } from "k6";

const apiUrl = __ENV.API_URL;
const webUrl = __ENV.WEB_URL;
if (!apiUrl || !webUrl) {
  throw new Error("API_URL and WEB_URL are required. There is no default host.");
}

const password = __ENV.DEMO_PASSWORD || "demo1234";
const host = __ENV.HOST || "";

const studentEmails = [
  "student1@alnoor.school",
  "student2@alnoor.school",
  "student3@alnoor.school",
];
const teacherEmail = "meera.krishnan@alnoor.school";
const adminEmail = "fatima.almansouri@alnoor.school";

function arrival(name, rate, startTime) {
  return {
    executor: "constant-arrival-rate",
    exec: "load",
    rate,
    timeUnit: "1s",
    duration: "2m",
    startTime,
    preAllocatedVUs: 40,
    maxVUs: 180,
    tags: { stage: name },
  };
}

export const options = {
  scenarios: {
    r10: arrival("10", 10, "0s"),
    r50: arrival("50", 50, "2m"),
    r100: arrival("100", 100, "4m"),
    r200: arrival("200", 200, "6m"),
    r300: arrival("300", 300, "8m"),
    r400: arrival("400", 400, "10m"),
    idle: {
      executor: "constant-vus",
      exec: "idle",
      vus: 1,
      duration: "6m",
      startTime: "12m",
      tags: { stage: "0" },
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<800"],
    http_req_failed: ["rate<0.01"],
  },
};

function headers(token) {
  const result = { "Content-Type": "application/json" };
  if (host) {
    result.Host = host;
  }
  if (token) {
    result.Authorization = `Bearer ${token}`;
  }
  return result;
}

function login(email) {
  const response = http.post(
    `${apiUrl}/api/v1/auth/login`,
    JSON.stringify({ email, password }),
    { headers: headers(), tags: { stage: "setup" } },
  );
  if (response.status !== 200) {
    throw new Error(`login failed for ${email}: ${response.status}`);
  }
  return response.json("access_token");
}

export function setup() {
  const students = studentEmails.map((email) => login(email));
  const teacher = login(teacherEmail);
  const admin = login(adminEmail);
  const conversations = [];
  for (let index = 0; index < 8; index += 1) {
    const created = http.post(
      `${apiUrl}/api/v1/chats`,
      JSON.stringify({ title: `load ${index}` }),
      { headers: headers(admin), tags: { stage: "setup" } },
    );
    if (created.status !== 201) {
      throw new Error(`create chat failed: ${created.status}`);
    }
    conversations.push(created.json("id"));
  }
  return { students, teacher, admin, conversations };
}

const reads = [
  "/api/v1/materials",
  "/api/v1/me/enrollments",
  "/api/v1/me/learning-directory",
  "/api/v1/me/feedback",
];

export function load(data) {
  const roll = Math.random();
  if (roll < 0.6) {
    const token = data.students[Math.floor(Math.random() * data.students.length)];
    if (Math.random() < 0.2) {
      const response = http.get(`${apiUrl}/api/v1/insights/dashboard`, {
        headers: headers(data.teacher),
      });
      check(response, { "dashboard 200": (item) => item.status === 200 });
      return;
    }
    const path = reads[Math.floor(Math.random() * reads.length)];
    const response = http.get(`${apiUrl}${path}`, { headers: headers(token) });
    check(response, { "read 200": (item) => item.status === 200 });
    return;
  }
  if (roll < 0.85) {
    if (Math.random() < 0.5) {
      const response = http.get(`${apiUrl}/health/ready`, { headers: headers() });
      check(response, { "ready 200": (item) => item.status === 200 });
      return;
    }
    const path = Math.random() < 0.5 ? "/" : "/favicon.png";
    const response = http.get(`${webUrl}${path}`, { headers: headers() });
    check(response, { "web 200": (item) => item.status === 200 });
    return;
  }
  const conversation =
    data.conversations[Math.floor(Math.random() * data.conversations.length)];
  const response = http.post(
    `${apiUrl}/api/v1/chats/${conversation}/messages`,
    JSON.stringify({ content: "What is the attendance eligibility rule?" }),
    { headers: headers(data.admin) },
  );
  check(response, { "chat 200": (item) => item.status === 200 });
}

export function idle() {
  sleep(5);
}

function stageRow(data, stage, seconds) {
  const duration = data.metrics[`http_req_duration{stage:${stage}}`];
  const requests = data.metrics[`http_reqs{stage:${stage}}`];
  const failed = data.metrics[`http_req_failed{stage:${stage}}`];
  const count = requests ? requests.values.count : 0;
  return {
    stage,
    rps: count / seconds,
    p50: duration ? duration.values["p(50)"] : null,
    p95: duration ? duration.values["p(95)"] : null,
    p99: duration ? duration.values["p(99)"] : null,
    error_rate: failed ? failed.values.rate : null,
  };
}

export function handleSummary(data) {
  const stages = [
    stageRow(data, "10", 120),
    stageRow(data, "50", 120),
    stageRow(data, "100", 120),
    stageRow(data, "200", 120),
    stageRow(data, "300", 120),
    stageRow(data, "400", 120),
    stageRow(data, "0", 360),
  ];
  const body = JSON.stringify({ stages });
  console.log(`K6_STAGE_SUMMARY ${body}`);
  return { stdout: `${JSON.stringify({ stages }, null, 2)}\n` };
}
