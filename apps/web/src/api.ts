import type {Session} from './types'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {super(message); this.status = status}
}
export async function api<T>(path: string, options: RequestInit = {}, session?: Session): Promise<T> {
  const response = await fetch(path, {...options, headers: {
    ...(options.body && !(options.body instanceof FormData) ? {'Content-Type': 'application/json'} : {}),
    ...(session ? {Authorization: `Bearer ${session.access_token}`} : {}), ...options.headers,
  }})
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const detail = typeof error.detail === 'string' ? error.detail : Array.isArray(error.detail)
      ? error.detail.map((e: {msg: string}) => e.msg).join('. ') : 'Something went wrong. Please try again.'
    throw new ApiError(detail, response.status)
  }
  return response.status === 204 ? undefined as T : response.json()
}
export async function authenticatedFile(path: string, session: Session) {
  const response = await fetch(path, {headers: {Authorization: `Bearer ${session.access_token}`}})
  if (!response.ok) throw new ApiError('Could not load this file. Refresh the workspace and try again.', response.status)
  return response.blob()
}
export function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = name; anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
