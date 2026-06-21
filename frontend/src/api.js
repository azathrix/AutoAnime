import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000
})

export async function getDashboard() {
  return (await api.get('/dashboard')).data
}

export async function getSettings() {
  return (await api.get('/settings')).data
}

export async function getDiagnostics() {
  return (await api.get('/system/diagnostics')).data
}

export async function saveSettings(payload) {
  return (await api.put('/settings', payload)).data
}

export async function getSeasonalEntry(id) {
  return (await api.get(`/seasonal/${id}`)).data
}

export async function saveSeasonalEntry(id, payload) {
  return (await api.put(`/seasonal/${id}`, payload)).data
}

export async function getLibraryEntry(id) {
  return (await api.get(`/library/${id}`)).data
}

export async function saveLibraryEntry(id, payload) {
  return (await api.put(`/library/${id}`, payload)).data
}

export async function getSeasonalItem(id) {
  return (await api.get(`/seasonal/${id}`)).data
}

export async function saveSeasonalItem(id, payload) {
  return (await api.put(`/seasonal/${id}`, payload)).data
}

export async function getLibraryItem(id) {
  return (await api.get(`/library/${id}`)).data
}

export async function saveLibraryItem(id, payload) {
  return (await api.put(`/library/${id}`, payload)).data
}

export async function postAction(path, payload = undefined) {
  return (await api.post(path, payload)).data
}

export async function deleteAction(path) {
  return (await api.delete(path)).data
}

