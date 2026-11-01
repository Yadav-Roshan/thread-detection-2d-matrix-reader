/** Map object-contain video layout: pointer coords ↔ native image pixels. */

export type FeedCrop = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type FeedImageLayout = {
  scale: number;
  /** Viewport (visible crop) top-left in the container. */
  offsetX: number;
  offsetY: number;
  displayWidth: number;
  displayHeight: number;
  /** Full native frame size. */
  imageWidth: number;
  imageHeight: number;
  /** Visible region in full-frame pixels. */
  viewX: number;
  viewY: number;
  viewWidth: number;
  viewHeight: number;
  /** Where to place the full <img> so the crop fills the viewport. */
  imageOffsetX: number;
  imageOffsetY: number;
  imageDisplayWidth: number;
  imageDisplayHeight: number;
};

export type CropHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'move';

export function clampCrop(crop: FeedCrop, imageWidth: number, imageHeight: number): FeedCrop {
  const maxW = Math.max(16, imageWidth);
  const maxH = Math.max(16, imageHeight);
  let width = Math.max(16, Math.min(crop.width, maxW));
  let height = Math.max(16, Math.min(crop.height, maxH));
  let x = Math.max(0, Math.min(crop.x, maxW - width));
  let y = Math.max(0, Math.min(crop.y, maxH - height));
  width = Math.min(width, maxW - x);
  height = Math.min(height, maxH - y);
  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.round(width),
    height: Math.round(height),
  };
}

export function computeFeedLayout(
  containerWidth: number,
  containerHeight: number,
  imageWidth: number,
  imageHeight: number,
  crop: FeedCrop | null,
  showFullFrame = false,
): FeedImageLayout | null {
  if (containerWidth <= 0 || containerHeight <= 0 || imageWidth <= 0 || imageHeight <= 0) {
    return null;
  }

  const view =
    !showFullFrame && crop
      ? clampCrop(crop, imageWidth, imageHeight)
      : { x: 0, y: 0, width: imageWidth, height: imageHeight };

  const scale = Math.min(containerWidth / view.width, containerHeight / view.height);
  const displayWidth = view.width * scale;
  const displayHeight = view.height * scale;
  const offsetX = (containerWidth - displayWidth) / 2;
  const offsetY = (containerHeight - displayHeight) / 2;

  return {
    scale,
    offsetX,
    offsetY,
    displayWidth,
    displayHeight,
    imageWidth,
    imageHeight,
    viewX: view.x,
    viewY: view.y,
    viewWidth: view.width,
    viewHeight: view.height,
    imageOffsetX: offsetX - view.x * scale,
    imageOffsetY: offsetY - view.y * scale,
    imageDisplayWidth: imageWidth * scale,
    imageDisplayHeight: imageHeight * scale,
  };
}

/** @deprecated Prefer computeFeedLayout — kept for callers that only need full-frame contain. */
export function computeObjectContainLayout(
  containerWidth: number,
  containerHeight: number,
  imageWidth: number,
  imageHeight: number,
): FeedImageLayout | null {
  return computeFeedLayout(containerWidth, containerHeight, imageWidth, imageHeight, null, true);
}

export function pointerToImageCoords(
  clientX: number,
  clientY: number,
  containerRect: DOMRect,
  layout: FeedImageLayout,
): { x: number; y: number } | null {
  const localX = clientX - containerRect.left - layout.offsetX;
  const localY = clientY - containerRect.top - layout.offsetY;
  if (
    localX < 0 ||
    localY < 0 ||
    localX > layout.displayWidth ||
    localY > layout.displayHeight
  ) {
    return null;
  }
  return {
    x: Math.round(layout.viewX + localX / layout.scale),
    y: Math.round(layout.viewY + localY / layout.scale),
  };
}

/** Rotate a crop 90° clockwise when the full frame size was (imageWidth × imageHeight). */
export function rotateCropClockwise(
  crop: FeedCrop,
  imageWidth: number,
  imageHeight: number,
): FeedCrop {
  return clampCrop(
    {
      x: imageHeight - crop.y - crop.height,
      y: crop.x,
      width: crop.height,
      height: crop.width,
    },
    imageHeight,
    imageWidth,
  );
}

export function handleSizeInImagePx(layout: FeedImageLayout): number {
  return Math.max(12, Math.round(14 / layout.scale));
}

function handleCenters(crop: FeedCrop): Record<Exclude<CropHandle, 'move'>, { x: number; y: number }> {
  const midX = crop.x + crop.width / 2;
  const midY = crop.y + crop.height / 2;
  const right = crop.x + crop.width;
  const bottom = crop.y + crop.height;
  return {
    nw: { x: crop.x, y: crop.y },
    n: { x: midX, y: crop.y },
    ne: { x: right, y: crop.y },
    e: { x: right, y: midY },
    se: { x: right, y: bottom },
    s: { x: midX, y: bottom },
    sw: { x: crop.x, y: bottom },
    w: { x: crop.x, y: midY },
  };
}

export function hitTestCropHandle(
  pt: { x: number; y: number },
  crop: FeedCrop,
  handlePx: number,
): CropHandle | null {
  const half = handlePx / 2;
  const centers = handleCenters(crop);
  const order: Exclude<CropHandle, 'move'>[] = [
    'nw',
    'ne',
    'sw',
    'se',
    'n',
    's',
    'e',
    'w',
  ];
  for (const key of order) {
    const c = centers[key];
    if (Math.abs(pt.x - c.x) <= half && Math.abs(pt.y - c.y) <= half) {
      return key;
    }
  }
  if (
    pt.x >= crop.x &&
    pt.x <= crop.x + crop.width &&
    pt.y >= crop.y &&
    pt.y <= crop.y + crop.height
  ) {
    return 'move';
  }
  return null;
}

export function applyCropDrag(
  origin: FeedCrop,
  handle: CropHandle,
  startPt: { x: number; y: number },
  pt: { x: number; y: number },
  imageWidth: number,
  imageHeight: number,
): FeedCrop {
  const dx = pt.x - startPt.x;
  const dy = pt.y - startPt.y;
  const left0 = origin.x;
  const top0 = origin.y;
  const right0 = origin.x + origin.width;
  const bottom0 = origin.y + origin.height;

  let left = left0;
  let top = top0;
  let right = right0;
  let bottom = bottom0;

  switch (handle) {
    case 'move':
      return clampCrop(
        { x: left0 + dx, y: top0 + dy, width: origin.width, height: origin.height },
        imageWidth,
        imageHeight,
      );
    case 'nw':
      left = left0 + dx;
      top = top0 + dy;
      break;
    case 'n':
      top = top0 + dy;
      break;
    case 'ne':
      right = right0 + dx;
      top = top0 + dy;
      break;
    case 'e':
      right = right0 + dx;
      break;
    case 'se':
      right = right0 + dx;
      bottom = bottom0 + dy;
      break;
    case 's':
      bottom = bottom0 + dy;
      break;
    case 'sw':
      left = left0 + dx;
      bottom = bottom0 + dy;
      break;
    case 'w':
      left = left0 + dx;
      break;
    default: {
      const _exhaustive: never = handle;
      return _exhaustive;
    }
  }

  if (right - left < 16) {
    if (handle === 'nw' || handle === 'w' || handle === 'sw') left = right - 16;
    else right = left + 16;
  }
  if (bottom - top < 16) {
    if (handle === 'nw' || handle === 'n' || handle === 'ne') top = bottom - 16;
    else bottom = top + 16;
  }

  return clampCrop(
    { x: left, y: top, width: right - left, height: bottom - top },
    imageWidth,
    imageHeight,
  );
}

export function cropFromPoints(
  a: { x: number; y: number },
  b: { x: number; y: number },
  imageWidth: number,
  imageHeight: number,
): FeedCrop {
  return clampCrop(
    {
      x: Math.min(a.x, b.x),
      y: Math.min(a.y, b.y),
      width: Math.abs(b.x - a.x),
      height: Math.abs(b.y - a.y),
    },
    imageWidth,
    imageHeight,
  );
}

export function cursorForCropHandle(handle: CropHandle | null): string {
  switch (handle) {
    case 'nw':
    case 'se':
      return 'nwse-resize';
    case 'ne':
    case 'sw':
      return 'nesw-resize';
    case 'n':
    case 's':
      return 'ns-resize';
    case 'e':
    case 'w':
      return 'ew-resize';
    case 'move':
      return 'move';
    case null:
      return 'crosshair';
    default: {
      const _exhaustive: never = handle;
      return _exhaustive;
    }
  }
}
