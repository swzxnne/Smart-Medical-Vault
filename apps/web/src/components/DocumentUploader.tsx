"use client";

import { useRef, useState } from "react";
import { useApi } from "@/lib/api";
import type { MedicalDocument, UploadUrlResponse } from "@/lib/types";

const MAX_BYTES = 25 * 1024 * 1024;

// Browsers often leave file.type empty for DOCX/HEIC, so fall back to the extension.
const TYPE_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  heic: "image/heic",
  heif: "image/heif",
};
const ALLOWED_TYPES = new Set(Object.values(TYPE_BY_EXTENSION));

function contentTypeFor(file: File): string | null {
  if (ALLOWED_TYPES.has(file.type)) return file.type;
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return TYPE_BY_EXTENSION[ext] ?? null;
}

type ItemState = { id: string; name: string; state: "uploading" | "done" | "error"; message?: string };

interface Props {
  profileId: string;
  onUploaded: (doc: MedicalDocument) => void;
}

export function DocumentUploader({ profileId, onUploaded }: Props) {
  const api = useApi();
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<ItemState[]>([]);
  const [dragging, setDragging] = useState(false);

  function update(id: string, patch: Partial<ItemState>) {
    setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  async function uploadOne(file: File, id: string) {
    const contentType = contentTypeFor(file);
    if (!contentType) return update(id, { state: "error", message: "Unsupported file type" });
    if (file.size > MAX_BYTES) return update(id, { state: "error", message: "Larger than 25 MB" });

    try {
      const target = await api<UploadUrlResponse>("/documents/upload-url", {
        method: "POST",
        body: JSON.stringify({ profile_id: profileId, filename: file.name, content_type: contentType }),
      });
      const put = await fetch(target.upload_url, { method: "PUT", headers: target.headers, body: file });
      if (!put.ok) throw new Error(`Storage upload failed (${put.status})`);

      const doc = await api<MedicalDocument>("/documents", {
        method: "POST",
        body: JSON.stringify({ profile_id: profileId, storage_key: target.storage_key, original_name: file.name }),
      });
      update(id, { state: "done" });
      onUploaded(doc);
    } catch (err) {
      update(id, { state: "error", message: err instanceof Error ? err.message : "Upload failed" });
    }
  }

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    const batch = Array.from(files, (file) => ({ file, id: crypto.randomUUID() }));
    setItems((prev) => [...prev, ...batch.map(({ file, id }) => ({ id, name: file.name, state: "uploading" as const }))]);
    // Sequential keeps things simple and gentle on the local stack.
    for (const { file, id } of batch) await uploadOne(file, id);
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition ${
          dragging ? "border-teal-600 bg-teal-50" : "border-slate-300 bg-white hover:border-teal-500"
        }`}
      >
        <p className="font-medium text-slate-700">Drop reports here or click to choose</p>
        <p className="mt-1 text-sm text-slate-500">PDF, DOCX, JPG, PNG, WEBP, HEIC · up to 25 MB each</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          accept=".pdf,.docx,.jpg,.jpeg,.png,.webp,.heic,.heif"
          onChange={(e) => {
            handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {items.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {items.map((item) => (
            <li key={item.id} className="flex justify-between gap-4">
              <span className="truncate">{item.name}</span>
              <span
                className={
                  item.state === "error" ? "text-red-600" : item.state === "done" ? "text-teal-700" : "text-slate-500"
                }
              >
                {item.state === "uploading" ? "Uploading…" : item.state === "done" ? "Uploaded" : item.message}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
