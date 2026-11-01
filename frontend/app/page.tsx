'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Crosshair,
  Eraser,
  FileText,
  FolderOpen,
  ImageUp,
  Loader2,
  Lock,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  Trash2,
  X,
  XCircle,
} from 'lucide-react';
import { apiJson, apiUrl, postJson } from '@/lib/api';
import type {
  ActiveCameras,
  CameraDevice,
  CameraRole,
  CodeResult,
  DetectionParams,
  DevicesResponse,
  EndStatus,
  HerculesConfig,
  HerculesEventResult,
  HerculesStatus,
  InspectionResult,
  ReportRecord,
  ROI,
  RoiConfig,
  SaveState,
  Tab,
  ThreadResult,
} from '@/lib/types';
import {
  FeedOverlay,
  FeedStage,
  FeedToolbar,
  ReportPdfLink,
  SaveButton,
  SidePanel,
  StatusIcon,
  SuccessDialog,
  getRoiDisplay,
  type FeedOverlayMode,
} from '@/components/station';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';

const ROLES: CameraRole[] = ['code', 'thread'];

const ROLE_META: Record<CameraRole, { chip: string; short: string; roiLabel: string; upload: string }> = {
  code: { chip: '01 · 2D CODE CAMERA', short: '2D Code', roiLabel: '2D Code ROI', upload: '2D code image' },
  thread: { chip: '02 · THREAD CAMERA', short: 'Thread', roiLabel: 'Thread ROI', upload: 'Thread image' },
};

type ParamField = { key: keyof DetectionParams; label: string; step: number; integer: boolean; help: string };

const DETECTION_FIELDS: ParamField[] = [
  {
    key: 'min_ridge_area',
    label: 'Min thread-ridge area (px²)',
    step: 1000,
    integer: true,
    help: 'Area of detected thread ridges needed for PRESENT. Raise to be stricter; far/blurred views fall below it.',
  },
  {
    key: 'min_ridge_band_px',
    label: 'Min ridge band depth (px)',
    step: 5,
    integer: true,
    help: 'Radial depth of the thread zone. Small when the borescope is too far from the hole.',
  },
  {
    key: 'min_purity',
    label: 'Min ridge purity (0–1)',
    step: 0.01,
    integer: false,
    help: 'Share of fine texture that is thread-ordered. Plain bores, scratches and skin score low.',
  },
  {
    key: 'min_classifier_margin',
    label: 'Min classifier margin',
    step: 0.05,
    integer: false,
    help: 'Classifier confidence margin needed for PRESENT.',
  },
  {
    key: 'max_novelty',
    label: 'Max novelty',
    step: 0.1,
    integer: false,
    help: 'Rejects views unlike the training bores (hand, obstruction, wrong scene) as NOT VISIBLE. Lower = stricter.',
  },
];

const EMPTY_PARAMS: DetectionParams = {
  min_ridge_area: 0,
  min_ridge_band_px: 0,
  min_purity: 0,
  min_classifier_margin: 0,
  max_novelty: 0,
};

const DEFAULT_HERCULES: HerculesConfig = {
  enabled: false,
  bind_host: '0.0.0.0',
  port: 9000,
  ready_expect: 'isready',
  ready_reply: 'readyyes',
  not_ready_reply: 'readyno',
  scan_expect: 'scanjobno',
  scan_fail_reply: 'noread',
  thread_expect: 'threadpresence',
  thread_ok_reply: 'threadok',
  thread_nok_reply: 'threadnok',
  error_reply: 'error',
};

type HerculesStringKey = Exclude<keyof HerculesConfig, 'enabled' | 'bind_host' | 'port'>;

const HERCULES_FIELDS: { key: HerculesStringKey; label: string; help?: string }[] = [
  { key: 'ready_expect', label: 'Expect (ready check)' },
  { key: 'ready_reply', label: 'Reply when ready', help: 'Sent only when both cameras are live.' },
  { key: 'not_ready_reply', label: 'Reply when not ready' },
  { key: 'scan_expect', label: 'Expect (scan job)' },
  {
    key: 'scan_fail_reply',
    label: 'Reply when 2D code not read',
    help: 'On success the reply is the decoded 2D number itself.',
  },
  { key: 'thread_expect', label: 'Expect (thread check)' },
  { key: 'thread_ok_reply', label: 'Reply thread OK' },
  { key: 'thread_nok_reply', label: 'Reply thread NOT OK' },
  { key: 'error_reply', label: 'Reply on error' },
];

const REPORT_STRUCTURE = '<folder>\\YYYY\\MM\\DD\\GO|NO_GO\\<part>_<HHMMSS>.pdf';

const NOT_VISIBLE_HINT = 'Thread not visible — move the camera closer / remove obstruction';

/** What the results panel / feeds display: a full CHECK result or a partial Hercules scan. */
type ResultView = {
  code: CodeResult | null;
  thread: ThreadResult | null;
  decoded_number: string | null;
  overall: 'GO' | 'NO_GO' | null;
  timestamp: string | null;
  report_url: string | null;
  report_id: string | null;
  code_image: string | null;
  thread_image: string | null;
};

function viewFromResult(r: InspectionResult | HerculesEventResult, partialScan = false): ResultView {
  return {
    code: r.code_result ?? null,
    thread: partialScan ? null : r.thread_result ?? null,
    decoded_number: r.decoded_number ?? r.code_result?.text ?? null,
    overall: partialScan ? null : r.overall_result ?? null,
    timestamp: r.timestamp ?? null,
    report_url: partialScan ? null : r.report_url ?? null,
    report_id: partialScan ? null : r.report_id ?? null,
    code_image: r.code_image ?? null,
    thread_image: partialScan ? null : r.thread_image ?? null,
  };
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function threadStateLabel(state: string | null | undefined): string {
  if (!state) return '—';
  return state === 'NOT_VISIBLE' ? 'NOT VISIBLE' : state;
}

function formatConfidence(c: number | null | undefined): string {
  if (c === null || c === undefined || Number.isNaN(c)) return '—';
  const pct = c <= 1 ? c * 100 : c;
  return `${Math.round(pct)}%`;
}

function noGoReason(v: ResultView): string | null {
  if (v.overall !== 'NO_GO') return null;
  const parts: string[] = [];
  if (v.code && !v.code.passed) parts.push('2D code not read');
  if (v.thread?.state === 'NOT_VISIBLE') parts.push('Thread not visible');
  else if (v.thread?.state === 'ABSENT') parts.push('Thread absent');
  return parts.join(' · ') || null;
}

function statusFor(busy: boolean, part: { passed: boolean } | null | undefined): EndStatus {
  if (busy) return 'processing';
  if (!part) return 'idle';
  return part.passed ? 'pass' : 'fail';
}

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'BUTTON' || el.isContentEditable;
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>('inspection');

  // Server-side cameras
  const [devices, setDevices] = useState<CameraDevice[]>([]);
  const [activeCams, setActiveCams] = useState<ActiveCameras>({ code: null, thread: null });
  const [streamEpoch, setStreamEpoch] = useState<Record<CameraRole, number>>({ code: 0, thread: 0 });
  const [cameraBusy, setCameraBusy] = useState<CameraRole | null>(null);
  const [frameSaveBusy, setFrameSaveBusy] = useState<CameraRole | null>(null);
  const [savedFrame, setSavedFrame] = useState<{ role: CameraRole; path: string } | null>(null);
  const [notice, setNotice] = useState('');

  // LIVE inspection (manual CHECK or Hercules)
  const [operator, setOperator] = useState('');
  const [liveView, setLiveView] = useState<ResultView | null>(null);
  const [showLiveResult, setShowLiveResult] = useState(false);
  const [isInspecting, setIsInspecting] = useState(false);
  const [inspectError, setInspectError] = useState('');

  // SETUP → Offline test
  const [offFiles, setOffFiles] = useState<Record<CameraRole, File | null>>({ code: null, thread: null });
  const [offUrls, setOffUrls] = useState<Record<CameraRole, string | null>>({ code: null, thread: null });
  const offUrlsRef = useRef(offUrls);
  offUrlsRef.current = offUrls;
  const fileInputs = useRef<Record<CameraRole, HTMLInputElement | null>>({ code: null, thread: null });
  const [offView, setOffView] = useState<ResultView | null>(null);
  const [showOffResult, setShowOffResult] = useState(false);
  const [offBusy, setOffBusy] = useState(false);
  const [offError, setOffError] = useState('');

  // ROIs
  const [savedRois, setSavedRois] = useState<RoiConfig>({ code: null, thread: null });
  const [roiDraft, setRoiDraft] = useState<RoiConfig>({ code: null, thread: null });
  const [roiDrawMode, setRoiDrawMode] = useState<CameraRole | null>(null);
  const [roiSaveState, setRoiSaveState] = useState<SaveState>('idle');
  const [roiSaveError, setRoiSaveError] = useState('');
  const [showRoiSavedDialog, setShowRoiSavedDialog] = useState(false);

  // Detection params
  const [params, setParams] = useState<DetectionParams>(EMPTY_PARAMS);
  const [paramsSaveState, setParamsSaveState] = useState<SaveState>('idle');
  const [paramsSaveError, setParamsSaveError] = useState('');
  const [showParamsSavedDialog, setShowParamsSavedDialog] = useState(false);
  const [paramsNote, setParamsNote] = useState('');

  // Hercules
  const [herculesConfig, setHerculesConfig] = useState<HerculesConfig>(DEFAULT_HERCULES);
  const [herculesSaveState, setHerculesSaveState] = useState<SaveState>('idle');
  const [herculesSaveError, setHerculesSaveError] = useState('');
  const [herculesStatus, setHerculesStatus] = useState<HerculesStatus | null>(null);
  const lastHerculesSeqRef = useRef<number | null>(null);

  // Setup panels / reports
  const [setupLeftPanel, setSetupLeftPanel] = useState<'rois' | 'offline' | 'reports'>('rois');
  const [setupRightPanel, setSetupRightPanel] = useState<'detection' | 'hercules'>('detection');
  const [reportPart, setReportPart] = useState('');
  const [reportLookupState, setReportLookupState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [reportLookupResults, setReportLookupResults] = useState<ReportRecord[]>([]);
  const [reportLookupError, setReportLookupError] = useState('');
  const [recentReports, setRecentReports] = useState<ReportRecord[]>([]);
  const [recentState, setRecentState] = useState<'idle' | 'loading' | 'error'>('idle');
  const [recentError, setRecentError] = useState('');
  const [reportDir, setReportDir] = useState('');
  const [reportDirDefault, setReportDirDefault] = useState('');
  const [browseBusy, setBrowseBusy] = useState(false);
  const [reportDirSaveState, setReportDirSaveState] = useState<SaveState>('idle');
  const [reportDirError, setReportDirError] = useState('');
  const [showReportDirSaved, setShowReportDirSaved] = useState(false);

  // Auth
  const [showAuthDialog, setShowAuthDialog] = useState(false);
  const [authPassword, setAuthPassword] = useState('');
  const [authError, setAuthError] = useState('');
  const [authBusy, setAuthBusy] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [changePasswordError, setChangePasswordError] = useState('');
  const [showPasswordChanged, setShowPasswordChanged] = useState(false);

  // ---------------------------------------------------------------- loaders
  const loadDevices = useCallback(async () => {
    try {
      const data = await apiJson<DevicesResponse>('/api/camera/devices');
      setDevices(data.devices ?? []);
      if (data.active) setActiveCams(data.active);
    } catch (e) {
      setNotice(`Camera list unavailable: ${(e as Error).message}`);
    }
  }, []);

  const loadRois = useCallback(async () => {
    try {
      const data = await apiJson<RoiConfig>('/api/config/rois');
      const rois = { code: data.code ?? null, thread: data.thread ?? null };
      setSavedRois(rois);
      setRoiDraft(rois);
    } catch (e) {
      setNotice(`Could not load ROIs: ${(e as Error).message}`);
    }
  }, []);

  const loadParams = useCallback(async () => {
    try {
      setParams(await apiJson<DetectionParams>('/api/config/detection'));
    } catch (e) {
      setParamsSaveError((e as Error).message);
    }
  }, []);

  const loadHerculesConfig = useCallback(async () => {
    try {
      setHerculesConfig({ ...DEFAULT_HERCULES, ...(await apiJson<HerculesConfig>('/api/config/hercules')) });
    } catch (e) {
      setHerculesSaveError((e as Error).message);
    }
  }, []);

  const loadRecentReports = useCallback(async () => {
    setRecentState('loading');
    setRecentError('');
    try {
      setRecentReports(await apiJson<ReportRecord[]>('/api/reports?limit=20'));
      setRecentState('idle');
    } catch (e) {
      setRecentState('error');
      setRecentError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    try {
      const saved = localStorage.getItem('operatorName');
      if (saved) setOperator(saved);
    } catch {
      /* storage unavailable */
    }
    void loadDevices();
    void loadRois();
  }, [loadDevices, loadRois]);

  const loadReportDir = useCallback(async () => {
    try {
      const data = await apiJson<{ directory: string | null; default?: string | null }>('/api/report/directory');
      setReportDir(data.directory ?? '');
      setReportDirDefault(data.default ?? '');
    } catch (e) {
      setReportDirError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'setup' && setupLeftPanel === 'reports') {
      void loadRecentReports();
      void loadReportDir();
    }
  }, [activeTab, setupLeftPanel, loadRecentReports, loadReportDir]);

  useEffect(() => {
    return () => {
      Object.values(offUrlsRef.current).forEach((u) => u && URL.revokeObjectURL(u));
    };
  }, []);

  // ---------------------------------------------------------------- Hercules status polling (~1 s)
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(apiUrl('/api/hercules/status'));
        if (!res.ok) throw new Error(String(res.status));
        const status: HerculesStatus = await res.json();
        if (cancelled) return;
        setHerculesStatus(status);
        const ev = status.last_event;
        const seq = ev?.seq ?? 0;
        if (lastHerculesSeqRef.current === null) {
          // Ignore events that happened before this page loaded.
          lastHerculesSeqRef.current = seq;
          return;
        }
        if (seq === lastHerculesSeqRef.current) return;
        lastHerculesSeqRef.current = seq;
        if (!ev?.result) return;
        if (ev.kind === 'scan') {
          setLiveView(viewFromResult(ev.result, true));
          setShowLiveResult(true);
          setInspectError('');
        } else if (ev.kind === 'thread') {
          setLiveView(viewFromResult(ev.result));
          setShowLiveResult(true);
          setInspectError('');
        }
      } catch {
        if (!cancelled) setHerculesStatus(null);
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // ---------------------------------------------------------------- cameras
  const handleCameraSelect = async (role: CameraRole, value: number) => {
    setCameraBusy(role);
    setNotice('');
    try {
      if (value < 0) {
        const data = await postJson<{ success: boolean; active: ActiveCameras }>('/api/camera/release', { role });
        if (data.active) setActiveCams(data.active);
      } else {
        const data = await postJson<{ success: boolean; message: string; active: ActiveCameras }>(
          '/api/camera/select',
          { role, index: value }
        );
        if (data.active) setActiveCams(data.active);
        if (!data.success) setNotice(data.message || `Failed to open camera for ${ROLE_META[role].short}`);
      }
      setStreamEpoch((e) => ({ ...e, [role]: e[role] + 1 }));
    } catch (e) {
      setNotice((e as Error).message);
      void loadDevices();
    } finally {
      setCameraBusy(null);
    }
  };

  const saveFrame = async (role: CameraRole) => {
    if (frameSaveBusy) return;
    setFrameSaveBusy(role);
    try {
      const data = await apiJson<{ success: boolean; path: string; filename: string; message?: string }>(
        `/api/camera/${role}/save-frame`,
        { method: 'POST' }
      );
      if (data.success) setSavedFrame({ role, path: data.path || data.filename });
      else setNotice(data.message || 'Failed to save frame');
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setFrameSaveBusy(null);
    }
  };

  // ---------------------------------------------------------------- LIVE inspection
  const rememberOperator = () => {
    try {
      localStorage.setItem('operatorName', operator);
    } catch {
      /* ignore */
    }
  };

  const runInspect = async () => {
    if (isInspecting) return;
    setInspectError('');
    setIsInspecting(true);
    setShowLiveResult(false);
    rememberOperator();
    try {
      const data = await postJson<InspectionResult>('/api/inspect', { operator });
      setLiveView(viewFromResult(data));
      setShowLiveResult(true);
    } catch (e) {
      setInspectError((e as Error).message);
      setLiveView(null);
    } finally {
      setIsInspecting(false);
    }
  };

  // ---------------------------------------------------------------- SETUP offline test
  const offlineReady = !!offFiles.code && !!offFiles.thread;
  const offlineLoaded = !!offFiles.code || !!offFiles.thread;

  const handleFile = (role: CameraRole, file: File | null) => {
    const previous = offUrlsRef.current[role];
    if (previous) URL.revokeObjectURL(previous);
    setOffFiles((f) => ({ ...f, [role]: file }));
    setOffUrls((u) => ({ ...u, [role]: file ? URL.createObjectURL(file) : null }));
    setShowOffResult(false);
    setOffView(null);
    setOffError('');
  };

  const clearOfflineImages = () => {
    ROLES.forEach((role) => {
      const u = offUrlsRef.current[role];
      if (u) URL.revokeObjectURL(u);
    });
    setOffFiles({ code: null, thread: null });
    setOffUrls({ code: null, thread: null });
    setOffView(null);
    setShowOffResult(false);
    setOffError('');
  };

  const runOfflineCheck = async () => {
    if (!offlineReady || offBusy) return;
    setOffBusy(true);
    setOffError('');
    setShowOffResult(false);
    rememberOperator();
    try {
      const fd = new FormData();
      fd.append('code_image', offFiles.code as File);
      fd.append('thread_image', offFiles.thread as File);
      fd.append('operator', operator);
      const data = await apiJson<InspectionResult>('/api/inspect/offline', { method: 'POST', body: fd });
      setOffView(viewFromResult(data));
      setShowOffResult(true);
    } catch (e) {
      setOffError((e as Error).message);
      setOffView(null);
    } finally {
      setOffBusy(false);
    }
  };

  const dialogOpen =
    showAuthDialog ||
    showChangePassword ||
    showRoiSavedDialog ||
    showParamsSavedDialog ||
    showPasswordChanged ||
    showReportDirSaved ||
    savedFrame !== null;

  const runInspectRef = useRef(runInspect);
  runInspectRef.current = runInspect;

  // Enter / Space triggers CHECK when focus is not in a form control.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (activeTab !== 'inspection' || dialogOpen) return;
      if (e.key !== 'Enter' && e.key !== ' ') return;
      if (isTypingTarget(e.target) || e.repeat) return;
      e.preventDefault();
      void runInspectRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeTab, dialogOpen]);

  // ---------------------------------------------------------------- tabs / auth
  const handleTabChange = (tab: Tab) => {
    if (tab === 'setup') {
      if (activeTab === 'setup') return;
      setAuthError('');
      setAuthPassword('');
      setShowAuthDialog(true);
      return;
    }
    setRoiDrawMode(null);
    setActiveTab('inspection');
  };

  const handleLogin = async () => {
    if (!authPassword.trim() || authBusy) return;
    setAuthBusy(true);
    try {
      const data = await postJson<{ success: boolean; message: string }>('/api/auth/login', {
        password: authPassword,
      });
      if (data.success) {
        setShowAuthDialog(false);
        setAuthPassword('');
        setRoiDraft(savedRois);
        setParamsNote('');
        void loadParams();
        void loadHerculesConfig();
        setActiveTab('setup');
      } else {
        setAuthError(data.message || 'Invalid password');
      }
    } catch (e) {
      setAuthError((e as Error).message);
    } finally {
      setAuthBusy(false);
    }
  };

  const handleChangePassword = async () => {
    if (newPassword.length < 6) {
      setChangePasswordError('New password must be at least 6 characters');
      return;
    }
    if (newPassword !== confirmPassword) {
      setChangePasswordError('Passwords do not match');
      return;
    }
    try {
      const data = await postJson<{ success: boolean; message: string }>('/api/auth/change-password', {
        old_password: oldPassword,
        new_password: newPassword,
      });
      if (data.success) {
        setShowChangePassword(false);
        setOldPassword('');
        setNewPassword('');
        setConfirmPassword('');
        setChangePasswordError('');
        setShowPasswordChanged(true);
      } else {
        setChangePasswordError(data.message || 'Failed to change password');
      }
    } catch (e) {
      setChangePasswordError((e as Error).message);
    }
  };

  // ---------------------------------------------------------------- ROI editing
  const updateRoiField = (role: CameraRole, field: keyof ROI, raw: string) => {
    const value = Math.max(0, parseInt(raw, 10) || 0);
    setRoiDraft((r) => {
      const base = r[role] ?? { x: 0, y: 0, width: 0, height: 0 };
      return { ...r, [role]: { ...base, [field]: value } };
    });
  };

  const saveRois = async () => {
    for (const role of ROLES) {
      const roi = roiDraft[role];
      if (roi && (roi.width <= 0 || roi.height <= 0)) {
        setRoiSaveState('error');
        setRoiSaveError(`${ROLE_META[role].roiLabel}: width and height must be > 0 (Clear it to use the full frame).`);
        window.setTimeout(() => setRoiSaveState('idle'), 3000);
        return;
      }
    }
    setRoiSaveState('saving');
    setRoiSaveError('');
    try {
      const data = await postJson<RoiConfig>('/api/config/rois', roiDraft);
      const rois = { code: data.code ?? null, thread: data.thread ?? null };
      setSavedRois(rois);
      setRoiDraft(rois);
      setRoiSaveState('saved');
      setShowRoiSavedDialog(true);
      window.setTimeout(() => setRoiSaveState('idle'), 2500);
    } catch (e) {
      setRoiSaveState('error');
      setRoiSaveError((e as Error).message);
      window.setTimeout(() => setRoiSaveState('idle'), 3000);
    }
  };

  // ---------------------------------------------------------------- detection params
  const saveDetectionParams = async () => {
    setParamsSaveState('saving');
    setParamsSaveError('');
    try {
      setParams(await postJson<DetectionParams>('/api/config/detection', params));
      setParamsNote('');
      setParamsSaveState('saved');
      setShowParamsSavedDialog(true);
      window.setTimeout(() => setParamsSaveState('idle'), 2500);
    } catch (e) {
      setParamsSaveState('error');
      setParamsSaveError((e as Error).message);
      window.setTimeout(() => setParamsSaveState('idle'), 3000);
    }
  };

  const resetParamsToDefaults = async () => {
    try {
      setParams(await apiJson<DetectionParams>('/api/config/detection-defaults'));
      setParamsNote('Defaults loaded — click Save to apply.');
    } catch (e) {
      setParamsNote(`Could not load defaults: ${(e as Error).message}`);
    }
  };

  // ---------------------------------------------------------------- Hercules config
  const saveHerculesConfig = async () => {
    setHerculesSaveState('saving');
    setHerculesSaveError('');
    try {
      const data = await postJson<HerculesConfig>('/api/config/hercules', herculesConfig);
      setHerculesConfig({ ...DEFAULT_HERCULES, ...data });
      setHerculesSaveState('saved');
      window.setTimeout(() => setHerculesSaveState('idle'), 2500);
    } catch (e) {
      setHerculesSaveState('error');
      setHerculesSaveError((e as Error).message);
      window.setTimeout(() => setHerculesSaveState('idle'), 3000);
    }
  };

  // ---------------------------------------------------------------- reports
  const browseReportDir = async () => {
    setBrowseBusy(true);
    setReportDirError('');
    try {
      const data = await postJson<{ directory: string | null }>('/api/report/browse-directory', {});
      if (data.directory) setReportDir(data.directory);
    } catch (e) {
      setReportDirError((e as Error).message);
    } finally {
      setBrowseBusy(false);
    }
  };

  const saveReportDir = async (directory: string | null) => {
    setReportDirSaveState('saving');
    setReportDirError('');
    try {
      const data = await postJson<{ success: boolean; directory: string | null; message?: string }>(
        '/api/report/set-directory',
        { directory }
      );
      if (!data.success) throw new Error(data.message || 'Failed to save report folder');
      setReportDir(data.directory ?? '');
      setReportDirSaveState('saved');
      setShowReportDirSaved(true);
      window.setTimeout(() => setReportDirSaveState('idle'), 2500);
      void loadRecentReports();
    } catch (e) {
      setReportDirSaveState('error');
      setReportDirError((e as Error).message);
      window.setTimeout(() => setReportDirSaveState('idle'), 3000);
    }
  };

  const lookupReports = async () => {
    const part = reportPart.trim();
    if (!part) return;
    setReportLookupState('loading');
    setReportLookupError('');
    try {
      const data = await apiJson<ReportRecord[]>(`/api/reports/by-part/${encodeURIComponent(part)}`);
      setReportLookupResults(data);
      setReportLookupState('done');
    } catch (e) {
      setReportLookupResults([]);
      setReportLookupState('error');
      setReportLookupError((e as Error).message);
    }
  };

  // ---------------------------------------------------------------- feed helpers
  const inSetup = activeTab === 'setup';
  // Which result (if any) the centre feeds should reflect.
  // Offline results show only on the Offline test tab; ROIs are drawn on the original uploads / live feeds.
  const feedView: ResultView | null = inSetup
    ? showOffResult && setupLeftPanel === 'offline'
      ? offView
      : null
    : showLiveResult
      ? liveView
      : null;

  const streamSrc = (role: CameraRole) =>
    activeCams[role] !== null ? apiUrl(`/api/camera/${role}/stream?e=${streamEpoch[role]}`) : null;

  /** Uploaded offline images replace the live feed in SETUP while loaded. */
  const usesUpload = (role: CameraRole) => inSetup && !!offUrls[role];

  const annotatedFor = (role: CameraRole) => (role === 'code' ? feedView?.code_image : feedView?.thread_image) ?? null;

  const feedSrc = (role: CameraRole): string | null => {
    const annotated = annotatedFor(role);
    if (annotated) return annotated;
    if (usesUpload(role)) return offUrls[role];
    return streamSrc(role);
  };

  const roiDisplayFor = (role: CameraRole) => {
    const roi = inSetup ? roiDraft[role] : savedRois[role];
    const part = role === 'code' ? feedView?.code : feedView?.thread;
    return getRoiDisplay(roi, ROLE_META[role].roiLabel, part ? part.passed : null);
  };

  const overlayMode: FeedOverlayMode = feedView?.overall ? (feedView.overall === 'GO' ? 'go' : 'nogo') : 'none';
  const liveCount = ROLES.filter((r) => activeCams[r] !== null).length;

  const cameraFeedControl = (role: CameraRole) => {
    const active = activeCams[role];
    return (
      <div className="flex items-center gap-1.5 rounded-full border border-white/25 bg-black/55 py-1 pl-1 pr-1.5 text-white shadow-md">
        <select
          aria-label={`${ROLE_META[role].short} cam`}
          value={active ?? -1}
          onChange={(e) => void handleCameraSelect(role, Number(e.target.value))}
          onFocus={() => void loadDevices()}
          disabled={cameraBusy !== null}
          title={`Select server camera for ${ROLE_META[role].short}`}
          className="h-7 w-[10.5rem] max-w-full truncate rounded-full border border-white/20 bg-black/40 px-2.5 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 disabled:cursor-not-allowed disabled:opacity-60 [&>option]:bg-slate-900 [&>option]:text-white"
        >
          <option value={-1}>{devices.length === 0 ? 'No camera detected' : '— Off —'}</option>
          {devices.map((d) => (
            <option key={d.index} value={d.index}>
              {d.label}
            </option>
          ))}
        </select>
        {active !== null ? (
          <Badge variant="destructive" className="w-[4rem] shrink-0 justify-center bg-red-600 text-white">
            <span className="mr-1.5 inline-block h-2 w-2 animate-ping rounded-full bg-white" />
            LIVE
          </Badge>
        ) : (
          <Badge variant="secondary" className="w-[4rem] shrink-0 justify-center bg-white/15 text-slate-200">
            OFF
          </Badge>
        )}
      </div>
    );
  };

  const renderFeeds = () => (
    <main className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto bg-black lg:flex-row lg:overflow-hidden">
      {ROLES.map((role, i) => {
        const src = feedSrc(role);
        const resultShown = !!annotatedFor(role);
        const upload = usesUpload(role);
        const live = !resultShown && !upload;
        return (
          <div
            key={role}
            className={`flex min-h-[16rem] min-w-0 flex-1 flex-col lg:min-h-0 ${
              i === 0 ? 'border-b border-slate-800 lg:border-b-0 lg:border-r' : ''
            }`}
          >
            <FeedStage
              chip={upload ? `${ROLE_META[role].chip} · UPLOADED` : ROLE_META[role].chip}
              testId={`feed-${role}`}
              src={src}
              live={live && !!src}
              placeholder="Camera not connected"
              roiDisplay={roiDisplayFor(role)}
              drawEnabled={inSetup && roiDrawMode === role}
              onRoiDrawn={(roi) => setRoiDraft((r) => ({ ...r, [role]: roi }))}
              cameraControl={upload ? undefined : cameraFeedControl(role)}
              toolbar={
                <FeedToolbar
                  showSave={live && activeCams[role] !== null}
                  frameSaveBusy={frameSaveBusy === role}
                  onSaveFrame={() => void saveFrame(role)}
                  backLabel={resultShown ? (inSetup ? 'Show original' : 'Back to live') : undefined}
                  onBack={
                    resultShown ? () => (inSetup ? setShowOffResult(false) : setShowLiveResult(false)) : undefined
                  }
                />
              }
              overlay={
                role === 'thread' && feedView ? (
                  <FeedOverlay mode={overlayMode} detail={noGoReason(feedView)} reportUrl={feedView.report_url} />
                ) : undefined
              }
            />
          </div>
        );
      })}
    </main>
  );

  // ---------------------------------------------------------------- shared fragments
  const resultsBlock = (view: ResultView | null, busy: boolean, error: string, testId: string, emptyText: string) => (
    <div className="space-y-3" data-testid={testId}>
      <p className="text-xs font-bold uppercase tracking-wide text-slate-500">Results</p>
      <div className="flex items-center justify-between rounded-lg border bg-slate-50 p-3">
        <span className="font-semibold">2D Code</span>
        <StatusIcon status={statusFor(busy, view?.code)} />
      </div>
      <div className="flex items-center justify-between rounded-lg border bg-slate-50 p-3">
        <span className="font-semibold">Thread</span>
        <StatusIcon status={statusFor(busy, view?.thread)} />
      </div>

      {view && !busy ? (
        <div className="space-y-1">
          {view.code && (
            <p className="break-all text-sm text-muted-foreground">
              <span className="font-semibold text-slate-700">2D Code:</span>{' '}
              {view.code.passed
                ? `${view.code.text ?? view.decoded_number ?? ''}${view.code.format ? ` (${view.code.format})` : ''}`
                : `NOT READ${view.code.message ? ` — ${view.code.message}` : ''}`}
            </p>
          )}
          {view.thread ? (
            <p className="text-sm text-muted-foreground">
              <span className="font-semibold text-slate-700">Thread:</span> {threadStateLabel(view.thread.state)} ·{' '}
              {formatConfidence(view.thread.confidence)}
              {view.thread.message ? ` — ${view.thread.message}` : ''}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              <span className="font-semibold text-slate-700">Thread:</span> waiting for thread check…
            </p>
          )}
        </div>
      ) : (
        !error && !busy && <p className="text-sm text-muted-foreground">{emptyText}</p>
      )}

      {!busy && view?.thread?.state === 'NOT_VISIBLE' && (
        <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm font-medium text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{NOT_VISIBLE_HINT}</span>
        </div>
      )}

      {!busy && view?.report_url && (
        <ReportPdfLink
          reportUrl={view.report_url}
          reportId={view.report_id}
          className={`rounded-lg border p-3 ${
            view.overall === 'GO' ? 'border-teal-200 bg-teal-50' : 'border-red-200 bg-red-50'
          }`}
        />
      )}

      {error && (
        <div role="alert" className="flex gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );

  const reportCard = (record: ReportRecord) => {
    const isGo = record.overall_result === 'GO';
    return (
      <div key={record.report_id} className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="break-all font-semibold text-slate-800">{record.decoded_number ?? 'UNREAD'}</p>
            <p className="text-xs text-muted-foreground">{formatTimestamp(record.timestamp)}</p>
          </div>
          <Badge variant={isGo ? 'default' : 'destructive'} className="shrink-0">
            {isGo ? 'GO' : 'NO_GO'}
          </Badge>
        </div>
        {record.report_url ? (
          <a
            href={apiUrl(record.report_url)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm font-semibold text-teal-600 underline hover:text-teal-800"
          >
            <FileText className="h-4 w-4" />
            Open PDF report
          </a>
        ) : (
          <p className="text-xs text-amber-700">PDF file not available for this record.</p>
        )}
      </div>
    );
  };

  const herculesLine = (() => {
    const s = herculesStatus;
    if (!s || !s.enabled) return { text: 'Hercules: off', tone: 'text-slate-500' };
    if (s.client_connected)
      return { text: `Hercules: connected${s.client_addr ? ` (${s.client_addr})` : ''}`, tone: 'text-emerald-700' };
    if (s.listening) return { text: `Hercules: listening on :${s.port}`, tone: 'text-teal-700' };
    return { text: 'Hercules: not listening', tone: 'text-amber-700' };
  })();

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-100">
      <header className="z-40 shrink-0 border-b border-slate-200 bg-white shadow-sm">
        <div className="grid min-h-[5rem] grid-cols-[1fr_auto_1fr] items-center gap-3 px-6 py-3">
          <div className="flex min-w-0 items-center justify-start">
            {liveCount === 2 ? (
              <Badge variant="destructive" data-testid="live-badge" className="shrink-0 px-3 py-1.5 text-sm">
                <span className="mr-2 inline-block h-2 w-2 animate-ping rounded-full bg-red-600" />
                2/2 CAMERAS LIVE
              </Badge>
            ) : (
              <Badge variant="secondary" data-testid="live-badge" className="shrink-0 px-3 py-1.5 text-sm">
                {liveCount}/2 CAMERAS LIVE
              </Badge>
            )}
          </div>
          <h1 className="max-w-[min(92vw,48rem)] bg-gradient-to-r from-teal-600 to-teal-800 bg-clip-text px-2 text-center text-xl font-extrabold leading-tight tracking-tight text-transparent [text-wrap:balance] md:text-2xl lg:text-3xl xl:text-4xl">
            CAMSHAFT 2D MATRIX &amp; THREAD INSPECTION
          </h1>
          <div className="flex items-center justify-end">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/logo.jpeg"
              alt="Perfect Gauging Solutions"
              className="h-11 w-auto max-w-full shrink-0 object-contain md:h-12 lg:h-14"
            />
          </div>
        </div>
      </header>

      <div className="shrink-0 border-b border-slate-200 bg-white">
        <div className="flex items-center justify-between gap-4 px-6">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => handleTabChange('inspection')}
              className={`relative px-6 py-3 text-sm font-semibold transition-all ${
                activeTab === 'inspection'
                  ? 'border-b-2 border-teal-600 text-teal-600'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Play className="mr-2 inline-block h-4 w-4" />
              INSPECTION
            </button>
            <button
              type="button"
              onClick={() => handleTabChange('setup')}
              className={`relative px-6 py-3 text-sm font-semibold transition-all ${
                activeTab === 'setup' ? 'border-b-2 border-teal-600 text-teal-600' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Settings className="mr-2 inline-block h-4 w-4" />
              SETUP
            </button>
          </div>
          {activeTab === 'setup' && (
            <Button
              variant="outline"
              size="sm"
              className="shrink-0 border-teal-300"
              onClick={() => {
                setChangePasswordError('');
                setOldPassword('');
                setNewPassword('');
                setConfirmPassword('');
                setShowChangePassword(true);
              }}
            >
              <Lock className="mr-2 h-4 w-4" />
              Change password
            </Button>
          )}
        </div>
      </div>

      {notice && (
        <div
          role="alert"
          className="flex shrink-0 items-center gap-2 border-b border-red-200 bg-red-50 px-6 py-2 text-sm text-red-700"
        >
          <XCircle className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1">{notice}</span>
          <button
            type="button"
            aria-label="Dismiss"
            className="rounded p-1 hover:bg-red-100"
            onClick={() => setNotice('')}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {activeTab === 'inspection' ? (
          <>
            <SidePanel side="left" tabs={[{ id: 'station', label: 'Station' }]} active="station" onTabChange={() => {}}>
              <div className="space-y-4">
                <div className="rounded-lg border border-teal-200 bg-teal-50 p-4" data-testid="last-part">
                  <p className="text-xs font-bold uppercase text-teal-600">Last part</p>
                  <p className="mt-1 break-all font-mono text-xl font-bold text-teal-900">
                    {liveView ? liveView.decoded_number ?? 'UNREAD' : '—'}
                  </p>
                  {liveView ? (
                    <>
                      <p className="mt-1 text-sm text-teal-800">
                        {liveView.code?.format ? `${liveView.code.format} · ` : ''}
                        {liveView.timestamp ? formatTimestamp(liveView.timestamp) : ''}
                      </p>
                      <p className="mt-3 text-sm font-medium text-teal-700">
                        {liveView.overall
                          ? `${liveView.overall === 'GO' ? 'GO' : 'NO-GO'} · Thread ${threadStateLabel(liveView.thread?.state)}`
                          : 'Scanned · waiting for thread check'}
                      </p>
                    </>
                  ) : (
                    <p className="mt-1 text-sm text-teal-800">No part checked yet.</p>
                  )}
                </div>

                <div className="space-y-2 text-sm">
                  {ROLES.map((role) => {
                    const roi = savedRois[role];
                    return (
                      <div key={role} className="rounded-md border bg-slate-50 p-3" data-testid={`rule-${role}`}>
                        <p className="font-semibold text-slate-700">{ROLE_META[role].short} camera</p>
                        <p>{role === 'code' ? '2D code must be READ' : 'Thread must be PRESENT'}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {roi ? `ROI: set (${roi.width}×${roi.height} px)` : 'ROI: full frame'}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </div>
            </SidePanel>

            {renderFeeds()}

            <SidePanel side="right" tabs={[{ id: 'actions', label: 'Actions' }]} active="actions" onTabChange={() => {}}>
              <div className="flex h-full flex-col gap-4">
                <div className="space-y-1">
                  <Label htmlFor="operator">Operator</Label>
                  <Input
                    id="operator"
                    className="h-12 text-lg"
                    placeholder="Operator name"
                    value={operator}
                    onChange={(e) => setOperator(e.target.value)}
                  />
                </div>

                <div className="border-t border-slate-200 pt-4">
                  {resultsBlock(liveView, isInspecting, inspectError, 'results', 'Run CHECK to see results.')}
                </div>

                <p className={`text-sm ${herculesLine.tone}`} data-testid="hercules-line">
                  {herculesLine.text}
                </p>

                <div className="mt-auto space-y-3">
                  <Button
                    variant="success"
                    size="xl"
                    className="w-full shadow-lg"
                    disabled={isInspecting}
                    onClick={() => void runInspect()}
                  >
                    {isInspecting ? (
                      <>
                        <Loader2 className="mr-3 h-7 w-7 animate-spin" />
                        CHECKING…
                      </>
                    ) : (
                      <>
                        <ClipboardCheck className="mr-3 h-7 w-7" />
                        CHECK
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </SidePanel>
          </>
        ) : (
          <>
            <SidePanel
              side="left"
              tabs={[
                { id: 'rois', label: 'ROIs' },
                { id: 'offline', label: 'Offline test' },
                { id: 'reports', label: 'Reports' },
              ]}
              active={setupLeftPanel}
              onTabChange={(id) => {
                setSetupLeftPanel(id as 'rois' | 'offline' | 'reports');
                if (id !== 'rois') setRoiDrawMode(null);
              }}
            >
              {setupLeftPanel === 'rois' && (
                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Draw ROI on feed</p>
                  <p className="text-xs text-muted-foreground">
                    Optional. Select a region, then drag on the matching camera feed. No ROI = the full frame is
                    inspected.
                    {offlineLoaded ? ' Feeds currently show the Offline test images.' : ''}
                  </p>
                  {ROLES.map((role) => {
                    const roi = roiDraft[role];
                    return (
                      <div key={role} className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <Button
                          size="sm"
                          variant={roiDrawMode === role ? 'default' : 'outline'}
                          className="w-full justify-start text-left"
                          onClick={() => setRoiDrawMode((m) => (m === role ? null : role))}
                        >
                          <Crosshair className="mr-2 h-4 w-4" />
                          {ROLE_META[role].roiLabel}
                        </Button>
                        {roiDrawMode === role && (
                          <p className="rounded-md border border-teal-200 bg-teal-50 px-2 py-1.5 text-xs text-teal-800">
                            Drag on the {ROLE_META[role].short.toLowerCase()} feed to draw the box.
                          </p>
                        )}
                        <div className="grid grid-cols-4 gap-1.5">
                          {(
                            [
                              ['x', 'X'],
                              ['y', 'Y'],
                              ['width', 'W'],
                              ['height', 'H'],
                            ] as [keyof ROI, string][]
                          ).map(([field, short]) => (
                            <div key={field} className="space-y-1">
                              <Label className="text-[10px]" htmlFor={`roi-${role}-${field}`}>
                                {short}
                              </Label>
                              <Input
                                id={`roi-${role}-${field}`}
                                type="number"
                                min={0}
                                step={1}
                                className="h-9 px-1.5 text-xs"
                                placeholder="—"
                                value={roi ? roi[field] : ''}
                                onChange={(e) => updateRoiField(role, field, e.target.value)}
                              />
                            </div>
                          ))}
                        </div>
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs text-muted-foreground">
                            {roi ? `${roi.width}×${roi.height} px` : 'Full frame (no ROI)'}
                          </p>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-8 px-2 text-xs"
                            disabled={!roi}
                            onClick={() => setRoiDraft((r) => ({ ...r, [role]: null }))}
                          >
                            <Eraser className="mr-1 h-3.5 w-3.5" />
                            Clear (use full frame)
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                  <SaveButton state={roiSaveState} error={roiSaveError} label="Save ROIs" onClick={() => void saveRois()} />
                </div>
              )}

              {setupLeftPanel === 'offline' && (
                <div className="space-y-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Offline test</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Upload one image per camera and run a CHECK without the live cameras. Saved ROIs are applied and a
                      PDF report is written. While images are loaded the centre feeds show them.
                    </p>
                  </div>
                  <div className="space-y-2">
                    {ROLES.map((role) => (
                      <div key={role} className="space-y-1">
                        <input
                          ref={(el) => {
                            fileInputs.current[role] = el;
                          }}
                          type="file"
                          accept="image/*"
                          className="hidden"
                          aria-label={ROLE_META[role].upload}
                          data-testid={`upload-${role}`}
                          onChange={(e) => {
                            handleFile(role, e.target.files?.[0] ?? null);
                            e.target.value = '';
                          }}
                        />
                        <Button
                          variant="outline"
                          className="h-11 w-full justify-start"
                          onClick={() => fileInputs.current[role]?.click()}
                        >
                          {offFiles[role] ? (
                            <CheckCircle2 className="mr-2 h-4 w-4 shrink-0 text-emerald-600" />
                          ) : (
                            <ImageUp className="mr-2 h-4 w-4 shrink-0" />
                          )}
                          Upload {ROLE_META[role].upload}
                        </Button>
                        <p className="truncate text-xs text-muted-foreground" title={offFiles[role]?.name}>
                          {offFiles[role]?.name ?? 'No file chosen'}
                        </p>
                      </div>
                    ))}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="w-full"
                      disabled={!offlineLoaded}
                      onClick={clearOfflineImages}
                    >
                      <Trash2 className="mr-2 h-4 w-4" />
                      Clear images (back to live feeds)
                    </Button>
                  </div>
                  <Button
                    variant="success"
                    size="lg"
                    className="w-full shadow"
                    disabled={!offlineReady || offBusy}
                    onClick={() => void runOfflineCheck()}
                  >
                    {offBusy ? (
                      <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                    ) : (
                      <ClipboardCheck className="mr-2 h-5 w-5" />
                    )}
                    Run offline CHECK
                  </Button>
                  {!offlineReady && <p className="text-xs text-amber-700">Upload both images to enable the check.</p>}
                  <div className="border-t border-slate-200 pt-4">
                    {resultsBlock(offView, offBusy, offError, 'offline-results', 'No offline check run yet.')}
                  </div>
                </div>
              )}

              {setupLeftPanel === 'reports' && (
                <div className="space-y-4">
                  <div className="space-y-2 border-b border-slate-200 pb-4" data-testid="report-folder">
                    <Label htmlFor="report-dir">Report folder</Label>
                    <Input
                      id="report-dir"
                      className="h-10 font-mono text-xs"
                      placeholder={reportDirDefault || 'Default report folder'}
                      value={reportDir}
                      onChange={(e) => setReportDir(e.target.value)}
                    />
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        className="flex-1"
                        disabled={browseBusy}
                        onClick={() => void browseReportDir()}
                      >
                        {browseBusy ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <FolderOpen className="mr-2 h-4 w-4" />
                        )}
                        Browse…
                      </Button>
                      <div className="flex-1">
                        <SaveButton
                          state={reportDirSaveState}
                          label="Save"
                          onClick={() => void saveReportDir(reportDir.trim() || null)}
                        />
                      </div>
                    </div>
                    {reportDirError && <p className="text-xs text-red-600">{reportDirError}</p>}
                    <button
                      type="button"
                      className="text-xs font-semibold text-teal-600 underline hover:text-teal-800"
                      onClick={() => void saveReportDir(null)}
                    >
                      Use default
                    </button>
                    <p className="break-all text-xs text-muted-foreground">
                      Saved as {REPORT_STRUCTURE}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Report lookup</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Find inspection PDFs saved at CHECK time by the decoded 2D code (part number).
                    </p>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="report-part">Part number</Label>
                    <Input
                      id="report-part"
                      className="h-11"
                      placeholder="Enter decoded number"
                      value={reportPart}
                      onChange={(e) => setReportPart(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && void lookupReports()}
                    />
                  </div>
                  <Button
                    className="w-full"
                    disabled={reportLookupState === 'loading' || !reportPart.trim()}
                    onClick={() => void lookupReports()}
                  >
                    {reportLookupState === 'loading' ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Searching…
                      </>
                    ) : (
                      <>
                        <Search className="mr-2 h-4 w-4" />
                        Find reports
                      </>
                    )}
                  </Button>
                  {reportLookupError && <p className="text-sm text-red-600">{reportLookupError}</p>}
                  {reportLookupState === 'done' && (
                    <div className="space-y-2 border-t border-slate-200 pt-4" data-testid="lookup-results">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        {reportLookupResults.length} result{reportLookupResults.length === 1 ? '' : 's'}
                      </p>
                      {reportLookupResults.map(reportCard)}
                    </div>
                  )}

                  <div className="space-y-2 border-t border-slate-200 pt-4" data-testid="recent-reports">
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Recent</p>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-8 px-2"
                        title="Refresh"
                        aria-label="Refresh recent reports"
                        onClick={() => void loadRecentReports()}
                      >
                        <RefreshCw className={`h-4 w-4 ${recentState === 'loading' ? 'animate-spin' : ''}`} />
                      </Button>
                    </div>
                    {recentState === 'error' && <p className="text-sm text-red-600">{recentError}</p>}
                    {recentState !== 'error' && recentReports.length === 0 && (
                      <p className="text-sm text-muted-foreground">
                        {recentState === 'loading' ? 'Loading…' : 'No reports saved yet.'}
                      </p>
                    )}
                    {recentReports.map(reportCard)}
                  </div>
                </div>
              )}
            </SidePanel>

            {renderFeeds()}

            <SidePanel
              side="right"
              tabs={[
                { id: 'detection', label: 'Detection' },
                { id: 'hercules', label: 'Hercules' },
              ]}
              active={setupRightPanel}
              onTabChange={(id) => setSetupRightPanel(id as 'detection' | 'hercules')}
            >
              {setupRightPanel === 'hercules' ? (
                <div className="space-y-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Hercules client link
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      This application opens a TCP port and waits. Hercules connects as a client, then exchanges the
                      strings configured below. Results appear on the INSPECTION screen like a manual CHECK.
                    </p>
                  </div>
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="flex items-center justify-between">
                      <Label className="normal-case text-slate-800" htmlFor="hercules-enabled">
                        Accept Hercules connections
                      </Label>
                      <Switch
                        id="hercules-enabled"
                        aria-label="Accept Hercules connections"
                        checked={herculesConfig.enabled}
                        onCheckedChange={(v) => setHerculesConfig((c) => ({ ...c, enabled: v }))}
                      />
                    </div>
                  </div>
                  <div className={`space-y-3 ${!herculesConfig.enabled ? 'pointer-events-none opacity-40' : ''}`}>
                    <div className="space-y-1">
                      <Label htmlFor="hercules-bind_host">Listen address</Label>
                      <Input
                        id="hercules-bind_host"
                        className="h-10"
                        value={herculesConfig.bind_host}
                        disabled={!herculesConfig.enabled}
                        onChange={(e) => setHerculesConfig((c) => ({ ...c, bind_host: e.target.value }))}
                      />
                      <p className="text-xs text-muted-foreground">
                        Address this app binds on. Use 0.0.0.0 so Hercules on another PC can connect; 127.0.0.1 for
                        local-only.
                      </p>
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="hercules-port">Listen port</Label>
                      <Input
                        id="hercules-port"
                        type="number"
                        min={1}
                        max={65535}
                        step={1}
                        className="h-10"
                        value={herculesConfig.port}
                        disabled={!herculesConfig.enabled}
                        onChange={(e) =>
                          setHerculesConfig((c) => ({
                            ...c,
                            port: Math.min(65535, Math.max(1, parseInt(e.target.value, 10) || 1)),
                          }))
                        }
                      />
                      <p className="text-xs text-muted-foreground">
                        In Hercules, connect as TCP client to this PC&apos;s IP and this port.
                      </p>
                    </div>
                    <div className="space-y-3 border-t border-slate-200 pt-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Protocol strings
                      </p>
                      {HERCULES_FIELDS.map(({ key, label, help }) => (
                        <div key={key} className="space-y-1">
                          <Label htmlFor={`hercules-${key}`}>{label}</Label>
                          <Input
                            id={`hercules-${key}`}
                            className="h-10 font-mono"
                            value={herculesConfig[key]}
                            disabled={!herculesConfig.enabled}
                            onChange={(e) => setHerculesConfig((c) => ({ ...c, [key]: e.target.value }))}
                          />
                          {help && <p className="text-xs text-muted-foreground">{help}</p>}
                        </div>
                      ))}
                      <p className="text-xs text-muted-foreground">
                        Flow: Hercules sends &quot;{herculesConfig.ready_expect || '…'}&quot; → station replies &quot;
                        {herculesConfig.ready_reply || '…'}&quot; (both cameras live) / &quot;
                        {herculesConfig.not_ready_reply || '…'}&quot; → Hercules sends &quot;
                        {herculesConfig.scan_expect || '…'}&quot; → station replies the decoded 2D number / &quot;
                        {herculesConfig.scan_fail_reply || '…'}&quot; → Hercules sends &quot;
                        {herculesConfig.thread_expect || '…'}&quot; → station replies &quot;
                        {herculesConfig.thread_ok_reply || '…'}&quot; / &quot;{herculesConfig.thread_nok_reply || '…'}
                        &quot; and writes the PDF report. On failure: &quot;{herculesConfig.error_reply || '…'}&quot;.
                      </p>
                    </div>
                  </div>
                  <SaveButton
                    state={herculesSaveState}
                    error={herculesSaveError}
                    label="Save Hercules settings"
                    onClick={() => void saveHerculesConfig()}
                  />
                  <div
                    className="space-y-1 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs"
                    data-testid="hercules-status"
                  >
                    <p className="font-semibold uppercase tracking-wide text-muted-foreground">Status</p>
                    <p>
                      {herculesStatus?.listening
                        ? `Waiting for Hercules on ${herculesStatus.bind_host}:${herculesStatus.port}`
                        : herculesStatus?.enabled
                          ? 'Not listening'
                          : herculesStatus
                            ? 'Disabled'
                            : 'Status unavailable'}
                    </p>
                    <p>
                      Hercules client:{' '}
                      {herculesStatus?.client_connected ? herculesStatus.client_addr || 'connected' : 'none'}
                    </p>
                    <p>Cameras: {liveCount}/2 live</p>
                    {herculesStatus?.last_error && <p className="text-red-600">{herculesStatus.last_error}</p>}
                    {herculesStatus?.last_event && (
                      <p className="text-muted-foreground">
                        Last: {herculesStatus.last_event.kind}
                        {herculesStatus.last_event.message ? ` "${herculesStatus.last_event.message}"` : ''}
                        {herculesStatus.last_event.reply ? ` → ${herculesStatus.last_event.reply}` : ''}
                      </p>
                    )}
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="space-y-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Thread detection
                    </p>
                    {DETECTION_FIELDS.map(({ key, label, step, integer, help }) => (
                      <div key={key} className="space-y-1">
                        <Label className="normal-case" htmlFor={`param-${key}`}>
                          {label}
                        </Label>
                        <Input
                          id={`param-${key}`}
                          type="number"
                          step={step}
                          className="h-9"
                          value={Number.isFinite(params[key]) ? params[key] : 0}
                          onChange={(e) => {
                            const v = integer ? parseInt(e.target.value, 10) : parseFloat(e.target.value);
                            setParams((p) => ({ ...p, [key]: Number.isFinite(v) ? v : 0 }));
                          }}
                        />
                        <p className="text-xs text-muted-foreground">{help}</p>
                      </div>
                    ))}
                  </div>
                  <Button variant="outline" className="w-full" onClick={() => void resetParamsToDefaults()}>
                    <RotateCcw className="mr-2 h-4 w-4" />
                    Reset to defaults
                  </Button>
                  {paramsNote && <p className="text-center text-xs text-amber-700">{paramsNote}</p>}
                  <SaveButton
                    state={paramsSaveState}
                    error={paramsSaveError}
                    label="Save detection params"
                    onClick={() => void saveDetectionParams()}
                  />
                </div>
              )}
            </SidePanel>
          </>
        )}
      </div>

      <Dialog open={showAuthDialog} onOpenChange={setShowAuthDialog}>
        <DialogContent className="mx-4 sm:max-w-md">
          <DialogHeader>
            <DialogTitle>SETUP Authentication</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Enter password to access ROIs, offline test, reports, detection and Hercules settings.
          </p>
          <div className="space-y-1">
            <Label className="normal-case" htmlFor="setup-password">
              Password
            </Label>
            <Input
              id="setup-password"
              type="password"
              className="h-11"
              placeholder="Enter password"
              autoFocus
              value={authPassword}
              onChange={(e) => setAuthPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void handleLogin()}
            />
          </div>
          {authError && <p className="text-sm text-red-600">{authError}</p>}
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setShowAuthDialog(false)}>
              Cancel
            </Button>
            <Button onClick={() => void handleLogin()} disabled={!authPassword.trim() || authBusy}>
              Login
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <SuccessDialog open={showRoiSavedDialog} onOpenChange={setShowRoiSavedDialog} title="ROIs saved">
        2D code and thread regions were saved successfully.
      </SuccessDialog>

      <SuccessDialog open={showParamsSavedDialog} onOpenChange={setShowParamsSavedDialog} title="Detection parameters saved">
        Thread detection settings were saved successfully.
      </SuccessDialog>

      <SuccessDialog open={showReportDirSaved} onOpenChange={setShowReportDirSaved} title="Report folder saved">
        <p>New PDF reports will be saved to:</p>
        <p className="mt-1 break-all font-mono text-xs">{reportDir || reportDirDefault || 'the default folder'}</p>
      </SuccessDialog>

      <SuccessDialog open={showPasswordChanged} onOpenChange={setShowPasswordChanged} title="Password updated">
        SETUP password updated successfully.
      </SuccessDialog>

      <SuccessDialog open={savedFrame !== null} onOpenChange={(o) => !o && setSavedFrame(null)} title="Frame saved">
        <p>{savedFrame ? `${ROLE_META[savedFrame.role].short} camera frame saved to:` : ''}</p>
        <p className="mt-1 break-all font-mono text-xs">{savedFrame?.path}</p>
      </SuccessDialog>

      <Dialog open={showChangePassword} onOpenChange={setShowChangePassword}>
        <DialogContent className="mx-4 sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Change SETUP password</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This password is required to open the SETUP tab. Minimum 6 characters for the new password.
          </p>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="normal-case">Current password</Label>
              <Input
                type="password"
                className="h-11"
                placeholder="Enter current password"
                value={oldPassword}
                onChange={(e) => setOldPassword(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="normal-case">New password</Label>
              <Input
                type="password"
                className="h-11"
                placeholder="Enter new password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="normal-case">Confirm new password</Label>
              <Input
                type="password"
                className="h-11"
                placeholder="Re-enter new password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && void handleChangePassword()}
              />
            </div>
            {changePasswordError && <p className="text-sm font-medium text-red-600">{changePasswordError}</p>}
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setShowChangePassword(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleChangePassword()}
              disabled={!oldPassword || !newPassword || !confirmPassword}
            >
              Update password
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
