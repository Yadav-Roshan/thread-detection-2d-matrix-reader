'use client';

/**
 * Station building blocks ported from cut-mark-station (app/page.tsx) so both
 * apps share the same panels, feed stage, toolbar, overlay and save-button UX.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, FileText, Loader2, Save, Video, XCircle } from 'lucide-react';
import { apiUrl } from '@/lib/api';
import { computeFeedLayout, pointerToImageCoords, type FeedImageLayout } from '@/lib/feedLayout';
import type { EndStatus, ROI, SaveState } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';

export const ROI_COLORS = {
  default: '#0d9488',
  pass: '#16a34a',
  fail: '#dc2626',
  draft: '#22c55e',
};

export type RoiDisplay = { roi: ROI | null; color: string; label: string; lineWidth: number };

/** Same colour logic as the reference getRoiDisplay: teal idle, green pass, red fail. */
export function getRoiDisplay(roi: ROI | null, base: string, passed: boolean | null): RoiDisplay {
  if (passed === null) return { roi, color: ROI_COLORS.default, label: base, lineWidth: 3 };
  if (!passed) return { roi, color: ROI_COLORS.fail, label: `${base} — FAIL`, lineWidth: 4 };
  return { roi, color: ROI_COLORS.pass, label: base, lineWidth: 3 };
}

export function StatusIcon({ status }: { status: EndStatus }) {
  switch (status) {
    case 'pass':
      return <CheckCircle2 className="h-8 w-8 text-emerald-500" aria-label="pass" />;
    case 'fail':
      return <XCircle className="h-8 w-8 text-red-500" aria-label="fail" />;
    case 'processing':
      return <Loader2 className="h-8 w-8 animate-spin text-amber-500" aria-label="processing" />;
    default:
      return <div className="h-8 w-8 rounded-full border-2 border-slate-300" aria-label="idle" />;
  }
}

export type PanelTab = { id: string; label: string };

export function SidePanel({
  tabs,
  active,
  onTabChange,
  children,
  side = 'left',
  bodyClassName,
}: {
  tabs: PanelTab[];
  active: string;
  onTabChange: (id: string) => void;
  children: React.ReactNode;
  side?: 'left' | 'right';
  bodyClassName?: string;
}) {
  return (
    <aside
      className={`flex h-full min-h-0 w-[min(100%,20rem)] shrink-0 flex-col bg-white ${
        side === 'left' ? 'border-r' : 'border-l'
      } border-slate-200`}
    >
      <div className="flex shrink-0 border-b border-slate-200 bg-slate-50">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => onTabChange(tab.id)}
            className={`flex-1 px-2 py-3 text-center text-xs font-bold uppercase tracking-wide transition-colors ${
              active === tab.id
                ? 'border-b-2 border-teal-600 bg-white text-teal-700'
                : 'text-slate-500 hover:bg-slate-100'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className={bodyClassName ?? 'min-h-0 flex-1 overflow-y-auto p-4'}>{children}</div>
    </aside>
  );
}

const TOOLBAR_BTN =
  'h-9 w-9 rounded-full border border-white/25 bg-black/55 text-white shadow-md hover:bg-black/75 disabled:opacity-40';

export function FeedToolbar({
  showSave,
  frameSaveBusy,
  onSaveFrame,
  backLabel,
  onBack,
}: {
  showSave: boolean;
  frameSaveBusy: boolean;
  onSaveFrame: () => void;
  backLabel?: string;
  onBack?: () => void;
}) {
  const saveLabel = 'Save current frame (PNG, no compression)';
  return (
    <div className="absolute right-2 top-2 z-30 flex items-center gap-1">
      {onBack && backLabel && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          title={backLabel}
          className="h-9 rounded-full border border-white/25 bg-black/55 px-3 text-xs font-semibold text-white shadow-md hover:bg-black/75 hover:text-white"
          onClick={onBack}
        >
          <Video className="mr-1.5 h-4 w-4" />
          {backLabel}
        </Button>
      )}
      {showSave && (
        <Button
          type="button"
          size="icon"
          variant="ghost"
          title={saveLabel}
          aria-label={saveLabel}
          disabled={frameSaveBusy}
          className={TOOLBAR_BTN}
          onClick={onSaveFrame}
        >
          {frameSaveBusy ? <Loader2 className="h-5 w-5 animate-spin" /> : <Save className="h-5 w-5" />}
        </Button>
      )}
    </div>
  );
}

export type FeedOverlayMode = 'none' | 'go' | 'nogo';

const FEED_OVERLAY_BOTTOM =
  'pointer-events-none absolute inset-0 z-20 flex items-end justify-center px-3 pb-4';

/** Reference ReportPdfLink: open the inspection PDF in a new tab. */
export function ReportPdfLink({
  reportUrl,
  reportId,
  className = '',
}: {
  reportUrl: string;
  reportId?: string | null;
  className?: string;
}) {
  return (
    <div className={className}>
      <a
        href={apiUrl(reportUrl)}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-sm font-semibold text-teal-600 underline hover:text-teal-800"
      >
        <FileText className="h-4 w-4" />
        Open inspection report (PDF)
      </a>
      {reportId && (
        <p className="mt-1 break-all text-xs text-muted-foreground" title={reportId}>
          {reportId}
        </p>
      )}
    </div>
  );
}

export function FeedOverlay({
  mode,
  detail,
  reportUrl,
}: {
  mode: FeedOverlayMode;
  detail?: string | null;
  reportUrl?: string | null;
}) {
  if (mode === 'none') return null;
  const isGo = mode === 'go';
  const shell = isGo
    ? 'border-emerald-300/90 bg-emerald-500/25 text-white'
    : 'border-red-300/90 bg-red-600/25 text-white';
  return (
    <div className={FEED_OVERLAY_BOTTOM}>
      <div
        data-testid="feed-overlay"
        className={`w-full max-w-sm rounded-xl border-2 px-4 py-3 text-center shadow-lg backdrop-blur-sm ${shell}`}
      >
        <p className="text-[10px] font-bold uppercase tracking-[0.25em] opacity-90">Result</p>
        <p className="mt-1 text-3xl font-black leading-none md:text-4xl">{isGo ? 'GO' : 'NO-GO'}</p>
        {detail && <p className="mt-2 text-xs font-semibold uppercase tracking-wide opacity-95">{detail}</p>}
        {mode === 'nogo' && reportUrl && (
          <div className="pointer-events-auto mt-2">
            <a
              href={apiUrl(reportUrl)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block rounded-md bg-white/20 px-2 py-1 text-xs font-semibold text-white underline hover:bg-white/30"
            >
              Open report PDF
            </a>
          </div>
        )}
      </div>
    </div>
  );
}

/** Save button with the reference amber / emerald / red / teal state colours. */
export function SaveButton({
  state,
  error,
  label,
  onClick,
  className = '',
}: {
  state: SaveState;
  error?: string;
  label: string;
  onClick: () => void;
  className?: string;
}) {
  return (
    <>
      <Button
        className={`w-full font-semibold text-white transition-colors ${
          state === 'saving'
            ? 'bg-amber-500 hover:bg-amber-500'
            : state === 'saved'
              ? 'bg-emerald-600 hover:bg-emerald-600'
              : state === 'error'
                ? 'bg-red-600 hover:bg-red-600'
                : 'bg-teal-600 hover:bg-teal-700'
        } ${className}`}
        disabled={state === 'saving'}
        onClick={onClick}
      >
        {state === 'saving' ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Saving…
          </>
        ) : state === 'saved' ? (
          <>
            <CheckCircle2 className="mr-2 h-4 w-4" />
            Saved
          </>
        ) : state === 'error' ? (
          'Save failed — retry'
        ) : (
          label
        )}
      </Button>
      {state === 'error' && error && <p className="text-center text-xs text-red-600">{error}</p>}
    </>
  );
}

export function SuccessDialog({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="mx-4 sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-emerald-700">
            <CheckCircle2 className="h-6 w-6" />
            {title}
          </DialogTitle>
        </DialogHeader>
        <div className="text-sm text-muted-foreground">{children}</div>
        <DialogFooter>
          <Button className="bg-teal-600 hover:bg-teal-700" onClick={() => onOpenChange(false)}>
            OK
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type Pt = { x: number; y: number };

function sameLayout(a: FeedImageLayout | null, b: FeedImageLayout | null): boolean {
  if (!a || !b) return a === b;
  return (
    a.scale === b.scale &&
    a.offsetX === b.offsetX &&
    a.offsetY === b.offsetY &&
    a.imageWidth === b.imageWidth &&
    a.imageHeight === b.imageHeight
  );
}

/**
 * Black feed stage with object-contain image, canvas ROI overlay and optional
 * drag-to-draw ROI (pointer coords mapped to natural image pixels).
 */
export function FeedStage({
  chip,
  testId,
  src,
  live,
  placeholder,
  roiDisplay,
  drawEnabled = false,
  onRoiDrawn,
  toolbar,
  overlay,
  cameraControl,
}: {
  /** Camera select + LIVE/OFF badge, rendered under the label chip (online mode). */
  cameraControl?: React.ReactNode;
  chip: string;
  testId: string;
  src: string | null;
  /** MJPEG stream: poll for layout because onLoad is unreliable for multipart images. */
  live: boolean;
  placeholder: string;
  roiDisplay: RoiDisplay | null;
  drawEnabled?: boolean;
  onRoiDrawn?: (roi: ROI) => void;
  toolbar?: React.ReactNode;
  overlay?: React.ReactNode;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [layout, setLayout] = useState<FeedImageLayout | null>(null);
  const [dragStart, setDragStart] = useState<Pt | null>(null);
  const [dragEnd, setDragEnd] = useState<Pt | null>(null);

  const updateLayout = useCallback(() => {
    const container = containerRef.current;
    const img = imageRef.current;
    if (!container || !img?.naturalWidth) return;
    const { width, height } = container.getBoundingClientRect();
    const next = computeFeedLayout(width, height, img.naturalWidth, img.naturalHeight, null, true);
    setLayout((prev) => (sameLayout(prev, next) ? prev : next));
  }, []);

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !layout) return;
    canvas.width = layout.viewWidth;
    canvas.height = layout.viewHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const px = (n: number) => Math.max(n, n / layout.scale);

    if (roiDisplay?.roi) {
      const { roi, color, label, lineWidth } = roiDisplay;
      ctx.strokeStyle = color;
      ctx.lineWidth = px(lineWidth);
      ctx.strokeRect(roi.x, roi.y, roi.width, roi.height);
      const fontPx = Math.round(px(14));
      ctx.font = `bold ${fontPx}px sans-serif`;
      const textY = Math.max(roi.y - fontPx * 0.4, fontPx + 2);
      ctx.fillStyle = color;
      ctx.fillRect(roi.x, textY - fontPx, ctx.measureText(label).width + fontPx * 0.5, fontPx * 1.15);
      ctx.fillStyle = '#ffffff';
      ctx.fillText(label, roi.x + fontPx * 0.25, textY);
    }

    if (dragStart && dragEnd) {
      ctx.strokeStyle = ROI_COLORS.draft;
      ctx.setLineDash([px(8), px(4)]);
      ctx.lineWidth = px(3);
      ctx.strokeRect(
        Math.min(dragStart.x, dragEnd.x),
        Math.min(dragStart.y, dragEnd.y),
        Math.abs(dragEnd.x - dragStart.x),
        Math.abs(dragEnd.y - dragStart.y)
      );
      ctx.setLineDash([]);
    }
  }, [layout, roiDisplay, dragStart, dragEnd]);

  useEffect(() => {
    drawCanvas();
  }, [drawCanvas]);

  useEffect(() => {
    setLayout(null);
  }, [src]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const ro = new ResizeObserver(() => updateLayout());
    ro.observe(container);
    return () => ro.disconnect();
  }, [updateLayout]);

  useEffect(() => {
    if (!src) return;
    const id = window.setInterval(() => {
      const img = imageRef.current;
      if (img?.complete && img.naturalWidth > 0) updateLayout();
    }, live ? 250 : 500);
    return () => window.clearInterval(id);
  }, [src, live, updateLayout]);

  const clampedPoint = (clientX: number, clientY: number): Pt | null => {
    const container = containerRef.current;
    if (!container || !layout) return null;
    const rect = container.getBoundingClientRect();
    const lx = Math.min(Math.max(clientX - rect.left - layout.offsetX, 0), layout.displayWidth);
    const ly = Math.min(Math.max(clientY - rect.top - layout.offsetY, 0), layout.displayHeight);
    return {
      x: Math.round(Math.min(layout.imageWidth, lx / layout.scale)),
      y: Math.round(Math.min(layout.imageHeight, ly / layout.scale)),
    };
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawEnabled || !layout || !containerRef.current) return;
    const pt = pointerToImageCoords(e.clientX, e.clientY, containerRef.current.getBoundingClientRect(), layout);
    if (!pt) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragStart(pt);
    setDragEnd(pt);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragStart) return;
    const pt = clampedPoint(e.clientX, e.clientY);
    if (pt) setDragEnd(pt);
  };

  const onPointerUp = () => {
    if (dragStart && dragEnd) {
      const roi: ROI = {
        x: Math.min(dragStart.x, dragEnd.x),
        y: Math.min(dragStart.y, dragEnd.y),
        width: Math.abs(dragEnd.x - dragStart.x),
        height: Math.abs(dragEnd.y - dragStart.y),
      };
      if (roi.width >= 4 && roi.height >= 4) onRoiDrawn?.(roi);
    }
    setDragStart(null);
    setDragEnd(null);
  };

  return (
    <div
      ref={containerRef}
      data-testid={testId}
      className="relative min-h-[12rem] min-w-0 flex-1 overflow-hidden bg-black"
    >
      <div className="pointer-events-none absolute left-2 top-2 z-30 flex flex-col items-start gap-1.5">
        <div className="flex items-center gap-1.5">
        <span className="rounded-md border border-white/15 bg-black/60 px-2 py-1 text-[11px] font-bold uppercase tracking-widest text-teal-200">
          {chip}
        </span>
        {src && roiDisplay && !roiDisplay.roi && (
          <span className="rounded-md border border-white/10 bg-black/40 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-300">
            Full frame
          </span>
        )}
        </div>
        {cameraControl && <div className="pointer-events-auto">{cameraControl}</div>}
      </div>
      {toolbar}
      {overlay}
      {src ? (
        <div
          className="absolute overflow-hidden"
          style={
            layout
              ? { left: layout.offsetX, top: layout.offsetY, width: layout.displayWidth, height: layout.displayHeight }
              : { inset: 0, width: '100%', height: '100%' }
          }
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={imageRef}
            src={src}
            alt={`${chip} feed`}
            className="absolute max-w-none"
            style={
              layout
                ? { left: 0, top: 0, width: layout.imageDisplayWidth, height: layout.imageDisplayHeight }
                : { inset: 0, width: '100%', height: '100%', objectFit: 'contain' }
            }
            onLoad={updateLayout}
          />
          {layout && (
            <canvas
              ref={canvasRef}
              className="absolute left-0 top-0 touch-none"
              style={{
                width: layout.displayWidth,
                height: layout.displayHeight,
                cursor: drawEnabled ? 'crosshair' : 'default',
              }}
              width={layout.viewWidth}
              height={layout.viewHeight}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
            />
          )}
        </div>
      ) : (
        <div className="flex h-full items-center justify-center px-4 text-center text-slate-500">{placeholder}</div>
      )}
    </div>
  );
}
