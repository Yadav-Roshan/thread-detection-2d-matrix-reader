export type Tab = 'inspection' | 'setup';
export type CameraRole = 'code' | 'thread';
export type EndStatus = 'idle' | 'processing' | 'pass' | 'fail';
export type SaveState = 'idle' | 'saving' | 'saved' | 'error';

export type ROI = { x: number; y: number; width: number; height: number };
export type RoiConfig = { code: ROI | null; thread: ROI | null };

export type CameraDevice = { index: number; label: string; width: number; height: number };
export type ActiveCameras = { code: number | null; thread: number | null };
export type DevicesResponse = { devices: CameraDevice[]; active: ActiveCameras };

export type DetectionParams = {
  min_ridge_area: number;
  min_ridge_band_px: number;
  min_purity: number;
  min_classifier_margin: number;
  max_novelty: number;
};

export type ThreadState = 'PRESENT' | 'ABSENT' | 'NOT_VISIBLE';

export type CodeResult = { passed: boolean; text: string | null; format: string | null; message: string };
export type ThreadResult = {
  passed: boolean;
  state: ThreadState;
  confidence: number | null;
  message: string;
  evidence?: Record<string, number | null>;
};

export type InspectionMode = 'online' | 'offline' | 'hercules';

export type InspectionResult = {
  timestamp: string;
  operator: string;
  mode: InspectionMode;
  overall_result: 'GO' | 'NO_GO';
  decoded_number: string | null;
  code_result: CodeResult;
  thread_result: ThreadResult;
  code_image?: string | null;
  thread_image?: string | null;
  report_id?: string | null;
  report_url?: string | null;
};

export type ReportRecord = {
  report_id: string;
  timestamp: string;
  decoded_number: string | null;
  overall_result: 'GO' | 'NO_GO' | string;
  report_url: string | null;
};

export type HerculesConfig = {
  enabled: boolean;
  bind_host: string;
  port: number;
  ready_expect: string;
  ready_reply: string;
  not_ready_reply: string;
  scan_expect: string;
  scan_fail_reply: string;
  thread_expect: string;
  thread_ok_reply: string;
  thread_nok_reply: string;
  error_reply: string;
};

export type HerculesEventKind = 'connect' | 'disconnect' | 'ready' | 'scan' | 'thread' | 'error';

/** scan events carry a partial result (code_result + code_image + decoded_number). */
export type HerculesEventResult = Partial<InspectionResult> & { code_result?: CodeResult };

export type HerculesStatus = {
  enabled: boolean;
  listening: boolean;
  bind_host: string;
  port: number;
  client_connected: boolean;
  client_addr?: string | null;
  last_error?: string | null;
  last_event?: {
    seq: number;
    kind: HerculesEventKind | string;
    message: string;
    reply?: string | null;
    timestamp?: string | null;
    result?: HerculesEventResult | null;
  } | null;
};
