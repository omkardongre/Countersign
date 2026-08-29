"use client";

// Client-side Google Drive Picker integration.
//
// This only lets the operator browse their own Drive and select a file id —
// the same file id that already flowed, unchanged, through open-form.tsx ->
// live-store.ts -> POST /api/cases -> drive_fetch_evidence (server-side, via
// the department MCP service account). This module never reads file bytes
// itself; it only returns the picked file's id and name.
//
// Requires two env vars (see web/README.md and checklist.md "before
// deployment" section for what to update when moving beyond localhost):
//   NEXT_PUBLIC_GOOGLE_CLIENT_ID  - OAuth 2.0 Web application client id
//   NEXT_PUBLIC_GOOGLE_API_KEY    - browser API key restricted to the
//                                   Drive API + Picker API

const DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly";

type PickedDriveFile = {
  id: string;
  name: string;
};

declare global {
  interface Window {
    gapi?: {
      load: (libraries: string, callback: () => void) => void;
    };
    google?: {
      accounts: {
        oauth2: {
          initTokenClient: (config: {
            client_id: string;
            scope: string;
            callback: (response: { access_token?: string; error?: string }) => void;
          }) => { requestAccessToken: () => void };
        };
      };
      picker: {
        Action: { PICKED: string; CANCEL: string };
        DocsView: new (viewId: string) => {
          setIncludeFolders: (v: boolean) => unknown;
          setSelectFolderEnabled: (v: boolean) => unknown;
        };
        ViewId: { DOCS: string };
        PickerBuilder: new () => {
          addView: (view: unknown) => unknown;
          setOAuthToken: (token: string) => unknown;
          setDeveloperKey: (key: string) => unknown;
          setCallback: (cb: (data: PickerResponse) => void) => unknown;
          build: () => { setVisible: (v: boolean) => void };
        };
      };
    };
  }
}

type PickerResponse = {
  action: string;
  docs?: { id: string; name: string }[];
};

let gapiScriptPromise: Promise<void> | null = null;
let gisScriptPromise: Promise<void> | null = null;
let pickerLibPromise: Promise<void> | null = null;

function loadScriptOnce(src: string, existingPromise: Promise<void> | null, save: (p: Promise<void>) => void): Promise<void> {
  if (existingPromise) return existingPromise;
  const promise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
    document.head.appendChild(script);
  });
  save(promise);
  return promise;
}

function loadPickerLibrary(): Promise<void> {
  if (pickerLibPromise) return pickerLibPromise;
  pickerLibPromise = loadScriptOnce(
    "https://apis.google.com/js/api.js",
    gapiScriptPromise,
    (p) => (gapiScriptPromise = p),
  ).then(
    () =>
      new Promise<void>((resolve, reject) => {
        if (!window.gapi) {
          reject(new Error("gapi did not load"));
          return;
        }
        window.gapi.load("picker", () => resolve());
      }),
  );
  return pickerLibPromise;
}

function loadIdentityServices(): Promise<void> {
  return loadScriptOnce(
    "https://accounts.google.com/gsi/client",
    gisScriptPromise,
    (p) => (gisScriptPromise = p),
  );
}

function requestAccessToken(clientId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!window.google) {
      reject(new Error("Google Identity Services did not load"));
      return;
    }
    const client = window.google.accounts.oauth2.initTokenClient({
      client_id: clientId,
      scope: DRIVE_READONLY_SCOPE,
      callback: (response) => {
        if (response.error || !response.access_token) {
          reject(new Error(response.error ?? "Drive authorization was not granted"));
          return;
        }
        resolve(response.access_token);
      },
    });
    client.requestAccessToken();
  });
}

/**
 * Opens the Google Drive Picker so the operator can browse their own Drive
 * and select one file, instead of pasting a raw file id. Resolves with the
 * picked file's id/name, or `null` if the user cancelled.
 *
 * Throws if NEXT_PUBLIC_GOOGLE_CLIENT_ID / NEXT_PUBLIC_GOOGLE_API_KEY are not
 * configured, or if the operator declines the Drive permission prompt.
 *
 * `onOpened` fires as soon as the picker UI is actually shown, independent
 * of how/whether it later resolves. The Google Picker does not reliably
 * fire a CANCEL callback when dismissed by clicking outside it (only when
 * its own Cancel affordance is used), so a caller must not gate a "busy"
 * UI state on this promise resolving — do that on `onOpened` instead.
 */
export async function openDrivePicker(
  onOpened?: () => void,
): Promise<PickedDriveFile | null> {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
  const apiKey = process.env.NEXT_PUBLIC_GOOGLE_API_KEY;
  if (!clientId || !apiKey) {
    throw new Error(
      "Drive picker is not configured: set NEXT_PUBLIC_GOOGLE_CLIENT_ID and " +
        "NEXT_PUBLIC_GOOGLE_API_KEY in web/.env.local.",
    );
  }

  await Promise.all([loadPickerLibrary(), loadIdentityServices()]);
  const accessToken = await requestAccessToken(clientId);

  return new Promise((resolve, reject) => {
    if (!window.google?.picker) {
      reject(new Error("Google Picker did not load"));
      return;
    }
    const { picker } = window.google;
    const view = new picker.DocsView(picker.ViewId.DOCS);
    view.setIncludeFolders(true);
    view.setSelectFolderEnabled(false);

    const builder = new picker.PickerBuilder();
    builder.addView(view);
    builder.setOAuthToken(accessToken);
    builder.setDeveloperKey(apiKey);
    builder.setCallback((data: PickerResponse) => {
      if (data.action === picker.Action.PICKED && data.docs && data.docs[0]) {
        resolve({ id: data.docs[0].id, name: data.docs[0].name });
      } else if (data.action === picker.Action.CANCEL) {
        resolve(null);
      }
    });

    builder.build().setVisible(true);
    onOpened?.();
  });
}
