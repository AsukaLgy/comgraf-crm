export const RTN_MESSAGE = 'El RTN debe contener exactamente 14 dígitos.'

export function isValidRtn(value) {
  return /^[0-9]{14}$/.test(value)
}

export function validateRtn(value) {
  if (value !== '' && !isValidRtn(value)) throw new Error(RTN_MESSAGE)
}
