/**
 * Forme Studio backend client.
 *
 * One thin wrapper around `fetch`. Server components pass an absolute URL via
 * NEXT_PUBLIC_BACKEND_URL (set in `next.config.mjs`); client components pick
 * it up from `process.env.NEXT_PUBLIC_BACKEND_URL` at build time.
 */

const BASE_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8002";

// --- types ---------------------------------------------------------------

export interface HealthCapabilities {
  openai_image: boolean;
  vectorizer_ai: boolean;
  inkscape: boolean;
  tesseract: boolean;
  cdr_enabled: boolean;
  cdr_cloudconvert: boolean;
  cdr_uniconvertor: boolean;
}

export type VectorizerProvider = "vectorizer_ai" | "inkscape_potrace";

export interface ProvidersSelected {
  vectorizer_primary: VectorizerProvider;
  vectorizer_fallback: VectorizerProvider | null;
  cdr_primary: "cloudconvert" | "uniconvertor";
  cdr_fallback: "cloudconvert" | "uniconvertor" | null;
}

export interface Health {
  status: "ok";
  version: string;
  image_model: string;
  capabilities: HealthCapabilities;
  providers: ProvidersSelected;
}

export interface PackagingPreset {
  id: string;
  label: string;
  description: string;
  trim_mm: { w: number; h: number };
  bleed_mm: number;
  dpi: number;
  color_space: string;
  generation_size: string;
  notes: string;
  is_builtin?: boolean;
}

export interface ProductTypeCreate {
  key: string;
  label: string;
  description?: string;
  trim_w_mm: number;
  trim_h_mm: number;
  bleed_mm?: number;
  dpi?: number;
  color_space?: string;
  generation_size?: string;
  notes?: string;
}

export type ProductTypeUpdate = Partial<Omit<ProductTypeCreate, "key">>;

export interface Workspace {
  id: number;
  slug: string;
  name: string;
  module: string;
  product_type: string;
  description: string | null;
  specs: Record<string, unknown>;
  design_mode: boolean;
  created_at: string;
  updated_at: string;
  folder_path: string;
}

export interface WorkspaceCreate {
  name: string;
  product_type: string;
  description?: string;
  slug?: string;
  /** Slice 10d: brainstorm-on-product flow when true; analyze-existing
   * (default) when false. Determines the studio's initial UX framing. */
  design_mode?: boolean;
}

export interface WorkspaceUpdate {
  name?: string;
  description?: string;
  design_mode?: boolean;
}

export interface WorkspaceDeleteRequest {
  /** When true, also rmtree the on-disk workspace folder. Defaults false. */
  delete_files?: boolean;
}

export interface WorkspaceDeleteResponse {
  slug: string;
  deleted_assets: number;
  deleted_audit_events: number;
  files_deleted: boolean;
}

export interface Asset {
  id: number;
  workspace_id: number;
  kind: "generation" | "export" | "reference";
  filename: string;
  relative_path: string;
  url: string;
  mime_type: string;
  size_bytes: number;
  prompt: string | null;
  model: string | null;
  image_size: string | null;
  quality: string | null;
  variant_index: number;
  provider_cost_usd: number;
  user_cost_usd: number;
  usage: Record<string, number>;
  created_at: string;
}

export interface GenerateRequest {
  prompt: string;
  n: number;
  quality: "low" | "medium" | "high" | "auto";
}

export interface GenerateResponse {
  assets: Asset[];
  provider_cost_usd: number;
  user_cost_usd: number;
  markup_percent: number;
}

export interface EditRequest {
  prompt: string;
  base_asset_id: number;
  reference_asset_ids?: number[];
  n?: number;
  quality?: "low" | "medium" | "high" | "auto";
}

export interface ReferenceUploadResponse {
  references: Asset[];
  total: number;
}

export interface PdfExportRequest {
  source_asset_id: number;
  dpi?: number;
  trim_marks?: boolean;
  registration_marks?: boolean;
}

export interface PdfExportResponse {
  asset: Asset;
  source_asset_id: number;
  trim_mm: { w: number; h: number };
  bleed_mm: number;
  dpi: number;
  icc_profile: string;
  icc_embedded: boolean;
  trim_marks: boolean;
  registration_marks: boolean;
}

export type VectorProvider = "vectorizer_ai" | "inkscape_potrace";

export interface VectorExportRequest {
  source_asset_id: number;
  /** Optional override of FORME_VECTORIZER_PROVIDER; the UI passes the
   *  fallback name here after the user clicks "Try with fallback?". */
  provider?: VectorProvider;
}

export interface VectorExportResponse {
  asset: Asset;
  source_asset_id: number;
  provider: VectorProvider;
  mode: string | null;
  size_bytes: number;
}

// ──────────────────────────────────────────────────── Composable PSD (slice 8/10)

export type ElementKind =
  | "graphic"
  | "wordmark"
  | "headline"
  | "ornament"
  | "seal"
  | "body_copy"
  | "text"; // slice 10a — OCR-discovered text region

export interface ElementSpec {
  name: string;
  label: string;
  prompt: string;
  /** [x, y, w, h] in millimetres, relative to trim top-left. */
  position_mm: [number, number, number, number];
  size_px: "1024x1024" | "1024x1536" | "1536x1024";
  kind: ElementKind;
  /** OCR'd text content (only for kind="text"). User can edit before assemble. */
  text?: string | null;
  /** OCR confidence 0-100; review UI flags low values. Only for kind="text". */
  confidence?: number | null;
  /** Hint from analyzer: should this element auto-vectorize? (slice 10c) */
  vectorizable?: boolean;
}

export interface ComposeDiscoverRequest {
  source_asset_id: number;
  extra_hint?: string;
}

export interface ComposeDiscoverResponse {
  source_asset_id: number;
  trim_mm: { w: number; h: number };
  elements: ElementSpec[];
  discovery_cost_usd: number;
  ocr_available: boolean;
  ocr_lang: string | null;
}

export interface ComposeAssembleRequest {
  source_asset_id: number;
  elements: ElementSpec[];
  quality?: "low" | "medium" | "high" | "auto";
  dpi?: number;
  color_space?: "CMYK" | "RGB";
  /** Auto-vectorize line-art elements during assembly (slice 10c). */
  vectorize?: boolean;
}

export interface ComposeElement {
  name: string;
  label: string;
  asset_id: number;
  width_px: number;
  height_px: number;
  cost_usd: number;
}

export interface ComposeAssembleResponse {
  asset: Asset;
  source_asset_id: number;
  element_count: number;
  layer_count: number;
  elements: ComposeElement[];
  total_cost_usd: number;
  dpi: number;
  color_space: string;
  width_px: number;
  height_px: number;
  /** Sibling SVG composite produced alongside the PSD (slice 10c). */
  svg_asset_id: number | null;
  svg_url: string | null;
  svg_vector_count: number;
  svg_raster_count: number;
  vector_cost_usd: number;
}

// Slice 10e — Design Round flatten
export interface DesignFlattenRequest {
  source_asset_id: number;
  quality?: "low" | "medium" | "high" | "auto";
}

export interface DesignFlattenResponse {
  asset: Asset;
  source_asset_id: number;
  flattened_from: number;
  provider_cost_usd: number;
  user_cost_usd: number;
}

export type CdrProvider = "cloudconvert" | "uniconvertor";

export interface CdrExportRequest {
  source_asset_id: number;
  /** Override the SVG generator (slice 6) for this call only. */
  vector_provider?: VectorProvider;
  /** Override the SVG→CDR converter for this call only. */
  cdr_provider?: CdrProvider;
}

export interface CdrExportResponse {
  asset: Asset;
  source_asset_id: number;
  vector_provider: VectorProvider;
  cdr_provider: CdrProvider;
  svg_size_bytes: number;
  cdr_size_bytes: number;
}

export interface SecretField {
  set: boolean;
  preview: string | null;
}

export interface SettingsOut {
  host: string;
  port: number;
  log_level: string;
  cors_origins: string[];
  workspaces_dir: string;
  db_path: string;
  openai_api_key: SecretField;
  vectorizer_ai_api_id: SecretField;
  vectorizer_ai_api_key: SecretField;
  cloudconvert_api_key: SecretField;
  cloudconvert_sandbox_api_key: SecretField;
  vectorizer_provider: "vectorizer_ai" | "inkscape_potrace";
  vectorizer_fallback: "vectorizer_ai" | "inkscape_potrace" | "none" | null;
  vectorizer_ai_mode: "production" | "test" | "preview";
  vectorizer_timeout_s: number;
  openai_image_model: string;
  pricing_markup_percent: number;
  image_generation_timeout_s: number;
  inkscape_path: string;
  inkscape_present: boolean;
  uniconvertor_path: string;
  uniconvertor_present: boolean;
  cdr_enabled: boolean;
  cdr_provider: CdrProvider;
  cdr_fallback: CdrProvider | "none" | null;
  cdr_timeout_s: number;
  cloudconvert_sandbox: boolean;
  tesseract_cmd: string;
  tesseract_present: boolean;
  tesseract_lang: string;
  print_icc_path: string;
  print_icc_present: boolean;
  print_icc_name: string;
  writable_keys: string[];
  env_file: string;
}

export interface SettingsPatch {
  vectorizer_provider?: "vectorizer_ai" | "inkscape_potrace";
  vectorizer_fallback?: "vectorizer_ai" | "inkscape_potrace" | "none";
  vectorizer_ai_mode?: "production" | "test" | "preview";
  vectorizer_timeout_s?: number;
  pricing_markup_percent?: number;
  openai_image_model?: string;
  image_generation_timeout_s?: number;
  inkscape_path?: string;
  uniconvertor_path?: string;
  cdr_enabled?: boolean;
  cdr_provider?: CdrProvider;
  cdr_fallback?: CdrProvider | "none";
  cdr_timeout_s?: number;
  cloudconvert_sandbox?: boolean;
  log_level?: "debug" | "info" | "warning" | "error";
  tesseract_cmd?: string;
  tesseract_lang?: string;
  print_icc_path?: string;
  print_icc_name?: string;
}

// --- helpers -------------------------------------------------------------

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { cache?: RequestCache }
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    // RSC fetches default to `force-cache`; for workspace lists we want fresh
    // data on every page load.
    cache: init?.cache ?? "no-store",
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

// --- endpoints -----------------------------------------------------------

export const api = {
  baseUrl: BASE_URL,

  health(): Promise<Health> {
    return request<Health>("/api/health");
  },

  listPresets(): Promise<PackagingPreset[]> {
    return request<PackagingPreset[]>("/api/packaging/presets");
  },

  // ---- product-types CRUD (configurable product presets) ----

  listProductTypes(): Promise<PackagingPreset[]> {
    return request<PackagingPreset[]>("/api/packaging/product-types");
  },

  createProductType(body: ProductTypeCreate): Promise<PackagingPreset> {
    return request<PackagingPreset>("/api/packaging/product-types", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  updateProductType(
    key: string,
    body: ProductTypeUpdate,
  ): Promise<PackagingPreset> {
    return request<PackagingPreset>(
      `/api/packaging/product-types/${key}`,
      { method: "PATCH", body: JSON.stringify(body) },
    );
  },

  async deleteProductType(key: string): Promise<void> {
    const res = await fetch(
      `${BASE_URL}/api/packaging/product-types/${key}`,
      { method: "DELETE" },
    );
    if (!res.ok && res.status !== 204) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        if (body?.detail) detail = String(body.detail);
      } catch {
        /* not json */
      }
      throw new ApiError(res.status, detail);
    }
  },

  listWorkspaces(): Promise<Workspace[]> {
    return request<Workspace[]>("/api/packaging/workspaces");
  },

  getWorkspace(slug: string): Promise<Workspace> {
    return request<Workspace>(`/api/packaging/workspaces/${slug}`);
  },

  createWorkspace(body: WorkspaceCreate): Promise<Workspace> {
    return request<Workspace>("/api/packaging/workspaces", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  updateWorkspace(
    slug: string,
    body: WorkspaceUpdate,
  ): Promise<Workspace> {
    return request<Workspace>(`/api/packaging/workspaces/${slug}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  deleteWorkspace(
    slug: string,
    body: WorkspaceDeleteRequest = {},
  ): Promise<WorkspaceDeleteResponse> {
    return request<WorkspaceDeleteResponse>(
      `/api/packaging/workspaces/${slug}`,
      { method: "DELETE", body: JSON.stringify(body) },
    );
  },

  listAssets(slug: string, kind?: string): Promise<Asset[]> {
    const q = kind ? `?kind=${encodeURIComponent(kind)}` : "";
    return request<Asset[]>(`/api/packaging/workspaces/${slug}/assets${q}`);
  },

  generate(slug: string, body: GenerateRequest): Promise<GenerateResponse> {
    return request<GenerateResponse>(
      `/api/packaging/workspaces/${slug}/generate`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  /** Absolute URL to an asset's file — usable directly in <img src=...>. */
  assetFileUrl(slug: string, assetId: number): string {
    return `${BASE_URL}/api/packaging/workspaces/${slug}/assets/${assetId}/file`;
  },

  /** SSE-streaming generate endpoint. Consumer in `lib/sse.ts`. */
  generateStreamUrl(slug: string): string {
    return `${BASE_URL}/api/packaging/workspaces/${slug}/generate/stream`;
  },

  /** SSE-streaming edit endpoint. Same consumer as generate. */
  editStreamUrl(slug: string): string {
    return `${BASE_URL}/api/packaging/workspaces/${slug}/edit/stream`;
  },

  getSettings(): Promise<SettingsOut> {
    return request<SettingsOut>("/api/settings");
  },

  patchSettings(patch: SettingsPatch): Promise<SettingsOut> {
    return request<SettingsOut>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  },

  exportPdf(slug: string, body: PdfExportRequest): Promise<PdfExportResponse> {
    return request<PdfExportResponse>(
      `/api/packaging/workspaces/${slug}/exports/pdf`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  exportVector(
    slug: string,
    body: VectorExportRequest,
  ): Promise<VectorExportResponse> {
    return request<VectorExportResponse>(
      `/api/packaging/workspaces/${slug}/exports/vector`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  exportCdr(
    slug: string,
    body: CdrExportRequest,
  ): Promise<CdrExportResponse> {
    return request<CdrExportResponse>(
      `/api/packaging/workspaces/${slug}/exports/cdr`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  // ─────────────────────────────────────── Composable PSD (slice 8)

  composeDiscover(
    slug: string,
    body: ComposeDiscoverRequest,
  ): Promise<ComposeDiscoverResponse> {
    return request<ComposeDiscoverResponse>(
      `/api/packaging/workspaces/${slug}/compose/discover`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  composeAssemble(
    slug: string,
    body: ComposeAssembleRequest,
  ): Promise<ComposeAssembleResponse> {
    return request<ComposeAssembleResponse>(
      `/api/packaging/workspaces/${slug}/exports/psd-composable`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  // Slice 10e — Design Round flatten
  designFlatten(
    slug: string,
    body: DesignFlattenRequest,
  ): Promise<DesignFlattenResponse> {
    return request<DesignFlattenResponse>(
      `/api/packaging/workspaces/${slug}/design/flatten`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  async uploadReferences(
    slug: string,
    files: File[],
  ): Promise<ReferenceUploadResponse> {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    const res = await fetch(
      `${BASE_URL}/api/packaging/workspaces/${slug}/references`,
      { method: "POST", body: form },
    );
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        if (body?.detail) detail = String(body.detail);
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as ReferenceUploadResponse;
  },
};

export { ApiError };
