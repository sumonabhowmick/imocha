// Who is signed in (admin and/or student). Sessions live in HttpOnly cookies; this only
// caches the profile the server returns.
import { get, post } from "./api.js";

export const session = { admin: null, student: null, adminChecked: false, studentChecked: false };

export async function loadAdmin(force = false) {
  if (session.adminChecked && !force) return session.admin;
  try { session.admin = await get("/api/auth/me"); } catch { session.admin = null; }
  session.adminChecked = true;
  return session.admin;
}

export async function loadStudent(force = false) {
  if (session.studentChecked && !force) return session.student;
  try { session.student = await get("/api/portal/me"); } catch { session.student = null; }
  session.studentChecked = true;
  return session.student;
}

export async function logoutAdmin() {
  try { await post("/api/auth/logout"); } catch { /* already signed out */ }
  session.admin = null;
}

export async function logoutStudent() {
  try { await post("/api/portal/logout"); } catch { /* already signed out */ }
  session.student = null;
}
