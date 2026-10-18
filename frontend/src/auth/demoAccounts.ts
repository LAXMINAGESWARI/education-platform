/** Accounts created by the synthetic Al Noor school. Password is `demo1234` for each. */

export const DEMO_PASSWORD = "demo1234";

export const DEMO_ADMIN = {
  role: "Admin",
  email: "fatima.almansouri@alnoor.school",
  detail: null,
  home: "/admin",
} as const;

export const DEMO_TEACHER = {
  role: "Teacher",
  email: "meera.krishnan@alnoor.school",
  detail: null,
  home: "/teacher",
} as const;

export const DEMO_STUDENT = {
  role: "Student",
  email: "student1@alnoor.school",
  detail: "Adnan, Grade 6",
  home: "/",
} as const;

export const DEMO_SECOND_STUDENT = {
  role: "Second student",
  email: "student97@alnoor.school",
  detail: "Aisha Rahman, Grade 8",
  home: "/",
} as const;

export const DEMO_ACCOUNTS = [
  DEMO_ADMIN,
  DEMO_TEACHER,
  DEMO_STUDENT,
  DEMO_SECOND_STUDENT,
] as const;

export type DemoAccount = (typeof DEMO_ACCOUNTS)[number];
