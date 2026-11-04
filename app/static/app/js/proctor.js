// In-browser proctoring. Face detection (MediaPipe BlazeFace) runs on the student's own
// device; only small event records are sent, plus one small JPEG for flagged events
// after the student consented. Events are for human review and never change a score.
import { post } from "./core/api.js";

const MP = "/static/vendor/mediapipe";
const CHECK_MS = 1500;
const MISSING_AFTER_MS = 5000;
const MULTI_COOLDOWN_MS = 30000;

let detectorPromise = null;
function loadDetector() {
  if (!detectorPromise) {
    detectorPromise = (async () => {
      const { FilesetResolver, FaceDetector } = await import(`${MP}/vision_bundle.mjs`);
      const fileset = await FilesetResolver.forVisionTasks(MP);
      return FaceDetector.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: `${MP}/blaze_face_short_range.tflite`, delegate: "CPU" },
        runningMode: "VIDEO",
        minDetectionConfidence: 0.5,
      });
    })().catch((err) => { detectorPromise = null; throw err; });
  }
  return detectorPromise;
}

export class Proctor {
  constructor({ onStatus } = {}) {
    this.onStatus = onStatus || (() => {});
    this.stream = null;
    this.video = null;
    this.detector = null;
    this.attemptId = null;
    this.timers = [];
    this.listeners = [];
    this.noFaceSince = null;
    this.missingReported = false;
    this.multiStreak = 0;
    this.lastMulti = 0;
    this.hiddenAt = null;
    this.lastFaces = null;
  }

  /** Ask for the camera and show it in `video`. Returns true if the camera works. */
  async startCamera(video) {
    this.video = video;
    if (!navigator.mediaDevices?.getUserMedia) { this.onStatus("Camera not supported in this browser", "bad"); return false; }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" }, audio: false });
    } catch (err) {
      this.onStatus(err?.name === "NotAllowedError" ? "Camera permission was denied" : "No camera found", "bad");
      return false;
    }
    video.srcObject = this.stream;
    video.muted = true;
    video.playsInline = true;
    await video.play().catch(() => {});
    this.onStatus("Camera on · loading face check…", "warn");
    try {
      this.detector = await loadDetector();
      this.onStatus("Camera on · face check ready", "good");
    } catch {
      this.detector = null;
      this.onStatus("Camera on · face check unavailable on this device", "warn");
    }
    return true;
  }

  /** Move the live preview to another <video> element (e.g. from setup to the test page). */
  attachPreview(video) {
    if (!this.stream) return;
    video.srcObject = this.stream;
    video.muted = true;
    video.playsInline = true;
    video.play().catch(() => {});
    this.video = video;
  }

  /** Start monitoring an attempt. */
  begin(attemptId, { cameraEnabled }) {
    this.attemptId = attemptId;
    if (cameraEnabled && this.stream) {
      this.send("camera_started", { details: { face_check: this.detector ? "on" : "unavailable" } });
      const track = this.stream.getVideoTracks()[0];
      const onEnded = () => { this.send("camera_stopped"); this.onStatus("Camera stopped", "bad"); };
      track?.addEventListener("ended", onEnded);
      this.listeners.push(() => track?.removeEventListener("ended", onEnded));
      if (this.detector) this.timers.push(setInterval(() => this.check(), CHECK_MS));
    }
    const onVisibility = () => {
      if (document.hidden) { this.hiddenAt = Date.now(); this.send("tab_hidden"); }
      else if (this.hiddenAt) { this.send("tab_visible", { duration_seconds: Math.round((Date.now() - this.hiddenAt) / 1000) }); this.hiddenAt = null; }
    };
    const onFullscreen = () => this.send(document.fullscreenElement ? "fullscreen_enter" : "fullscreen_exit");
    document.addEventListener("visibilitychange", onVisibility);
    document.addEventListener("fullscreenchange", onFullscreen);
    this.listeners.push(() => document.removeEventListener("visibilitychange", onVisibility), () => document.removeEventListener("fullscreenchange", onFullscreen));
  }

  check() {
    const v = this.video;
    if (!this.detector || !v || v.readyState < 2) return;
    let faces = 0;
    try { faces = this.detector.detectForVideo(v, performance.now()).detections.length; } catch { return; }
    const now = Date.now();
    if (faces !== this.lastFaces) {
      this.lastFaces = faces;
      this.onStatus(faces === 1 ? "Camera on · face visible" : faces === 0 ? "No face visible: stay in view of the camera" : "More than one face visible", faces === 1 ? "good" : "bad");
    }
    if (faces === 0) {
      this.noFaceSince ??= now;
      if (!this.missingReported && now - this.noFaceSince >= MISSING_AFTER_MS) {
        this.missingReported = true;
        this.send("face_missing", { snapshot: this.snapshot() });
      }
    } else {
      if (this.missingReported) this.send("face_returned", { duration_seconds: Math.round((now - this.noFaceSince) / 1000) });
      this.noFaceSince = null;
      this.missingReported = false;
    }
    this.multiStreak = faces >= 2 ? this.multiStreak + 1 : 0;
    if (this.multiStreak >= 2 && now - this.lastMulti > MULTI_COOLDOWN_MS) {
      this.lastMulti = now;
      this.send("multiple_faces", { details: { faces }, snapshot: this.snapshot() });
    }
  }

  snapshot() {
    const v = this.video;
    if (!v || !v.videoWidth) return undefined;
    const w = 320, hgt = Math.round((v.videoHeight / v.videoWidth) * w);
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = hgt;
    canvas.getContext("2d").drawImage(v, 0, 0, w, hgt);
    return canvas.toDataURL("image/jpeg", 0.6);
  }

  send(event_type, extra = {}) {
    if (!this.attemptId) return;
    post(`/api/portal/attempts/${this.attemptId}/proctoring/events`, { event_type, ...extra }).catch(() => { /* best effort */ });
  }

  stop() {
    this.timers.forEach(clearInterval);
    this.timers = [];
    this.listeners.forEach((off) => off());
    this.listeners = [];
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.attemptId = null;
  }
}
