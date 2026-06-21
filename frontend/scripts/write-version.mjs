import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const now = new Date()
const pad = value => String(value).padStart(2, '0')
const build = [
  now.getFullYear(),
  pad(now.getMonth() + 1),
  pad(now.getDate()),
  '-',
  pad(now.getHours()),
  pad(now.getMinutes()),
  pad(now.getSeconds()),
].join('')

const version = process.env.npm_package_version || '0.1.0'
const target = resolve('src/version.js')

writeFileSync(
  target,
  `export const APP_VERSION = '${version}'\nexport const APP_BUILD = '${build}'\n`,
  'utf8',
)

console.log(`AutoAnime frontend build ${version} · ${build}`)
