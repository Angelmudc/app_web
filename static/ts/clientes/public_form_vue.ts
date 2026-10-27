/* Source of truth for the progressive public-form controller.
 * The browser asset is generated from this file with the TypeScript compiler
 * described in tsconfig.public-form.json.
 */

type DraftValue = string | string[];

interface DraftEnvelope {
  schemaVersion: number;
  savedAt: number;
  values: Record<string, DraftValue>;
  serviceValues?: Record<string, Record<string, DraftValue>>;
}

interface DraftRestoreResult {
  restored: boolean;
}

interface FormError {
  element: FieldElement | HTMLElement;
  message: string;
  targetName?: string;
  validationKey?: string;
}

interface HTMLFormElement {
  __publicConditionsConfirmed?: boolean;
  _restoringDraft?: boolean;
  __serviceDraftState?: Record<string, Record<string, DraftValue>>;
}

type ScheduleTarget = 'horario_dias_trabajo' | 'horario_hora_entrada' | 'horario_hora_salida' | 'horario_dormida_entrada' | 'horario_dormida_salida';

interface SelectionGroup {
  name: string;
  message: string;
}

interface FormState {
  errors: FormError[];
  restored: boolean;
  submitting: boolean;
  dirty: boolean;
  serverFailure: boolean;
}

interface VueApi {
  reactive<T extends object>(value: T): T;
  computed<T>(getter: () => T): { readonly value: T };
  createApp(options: Record<string, unknown>): { mount(root: HTMLElement): void };
}

interface Window { Vue?: VueApi; }

const DRAFT_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const DRAFT_SCHEMA_VERSION = 4;
const LEGACY_DRAFT_SCHEMA_VERSION = 2;
const DRAFT_EXCLUDED_FIELDS = new Set([
  'csrf_token', 'token', 'codigo_solicitud', 'id', 'created_at', 'updated_at',
  'terms_accepted', 'terms_decision', 'terms_accepted_at', 'acepta_politica', 'lead_source',
  'hp', 'nombre_completo', 'email_contacto', 'telefono_contacto',
  'codigo_cliente', 'nombre_cliente', 'email_cliente',
]);

const MESSAGES: Record<string, string> = {
  nombre_completo: 'Escribe un nombre válido usando solo letras, espacios, guiones o apóstrofes.',
  nombre_cliente: 'Escribe un nombre válido usando solo letras, espacios, guiones o apóstrofes.',
  ciudad_input_ui: 'Indica la ciudad donde se realizará el trabajo.',
  sector_input_ui: 'Indica el sector o zona del hogar.',
  modalidad_grupo: 'Selecciona la modalidad de trabajo para continuar.',
  modalidad_especifica: 'Selecciona la modalidad que necesitas.',
  modalidad_otro_text: 'Describe brevemente la otra modalidad.',
  horario_dias_trabajo: 'Indica los días de trabajo.',
  horario_hora_entrada: 'Indica la hora de entrada.',
  horario_hora_salida: 'Indica la hora de salida.',
  horario_dormida_entrada: 'Indica cuándo entra la empleada.',
  horario_dormida_salida: 'Indica cuándo sale de descanso.',
  tipo_lugar: 'Indica qué tipo de lugar es.',
  habitaciones: 'Indica cuántas habitaciones tiene el hogar.',
  banos: 'Indica cuántos baños tiene el hogar.',
  sueldo: 'Indica el monto mensual que ofreces.',
  acepta_politica: 'Acepta los términos y la política de privacidad para continuar.',
  acepta_politica_nueva: 'Acepta los términos y la política de privacidad para continuar.',
  email_contacto: 'Escribe un correo válido, por ejemplo nombre@gmail.com.',
  telefono_contacto: 'Escribe un teléfono válido con al menos 7 dígitos, por ejemplo 809-000-0000.',
};

function normalizePersonName(value: string): string {
  return value.normalize('NFC').trim().replace(/\s+/g, ' ');
}

function isUnicodeLetter(value: string): boolean {
  return value.toUpperCase() !== value.toLowerCase();
}

function isValidPersonName(value: string): boolean {
  const normalized = normalizePersonName(value);
  if (!normalized || normalized.length > 200 || /(?:https?:\/\/|www\.|@)/i.test(normalized)) return false;
  const chars = Array.from(normalized);
  const separators = new Set([' ', '-', "'", '’']);
  if (!isUnicodeLetter(chars[0]) || !isUnicodeLetter(chars[chars.length - 1])) return false;
  if (chars.filter(isUnicodeLetter).length < 2) return false;
  for (let index = 0; index < chars.length; index += 1) {
    const char = chars[index];
    if (!isUnicodeLetter(char) && !separators.has(char)) return false;
    if (char === '-' || char === "'" || char === '’') {
      if (!isUnicodeLetter(chars[index - 1] || '') || !isUnicodeLetter(chars[index + 1] || '')) return false;
    }
  }
  return true;
}

// Checkbox/radio groups are one logical field even when WTForms renders one
// required attribute per option. Native validity must not count unchecked
// siblings as separate errors.
const SELECTION_GROUPS: SelectionGroup[] = [
  { name: 'edad_requerida', message: 'Selecciona el rango de edad que necesitas.' },
  { name: 'experiencia_visual', message: 'Selecciona una opción de experiencia.' },
  { name: 'funciones', message: 'Selecciona al menos una función.' },
  { name: 'envejeciente_tipo_cuidado', message: 'Indica si el envejeciente es independiente o está encamado.' },
  { name: 'areas_comunes', message: 'Selecciona las áreas comunes que deben atenderse.' },
  { name: 'modalidad_grupo', message: 'Selecciona la modalidad de trabajo para continuar.' },
  { name: 'pasaje_mode', message: 'Indica cómo se manejará el pasaje.' },
    { name: 'svc_care_assistance', message: 'Selecciona el tipo de asistencia requerida.' },
];

const SELECTION_GROUP_NAMES = new Set(SELECTION_GROUPS.map((group) => group.name));

const ELDER_RESPONSIBILITIES_MESSAGE = 'Indica al menos una responsabilidad de cuidado o selecciona que será solo acompañamiento/supervisión.';

type FieldElement = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

type HouseStructureTarget = 'tipo_lugar' | 'habitaciones_selector' | 'banos_selector' | 'areas_comunes';

const HOUSE_STRUCTURE_FIELD_NAMES = new Set(['tipo_lugar', 'habitaciones', 'banos', 'areas_comunes']);
const HOUSE_STRUCTURE_MESSAGES: Record<HouseStructureTarget, string> = {
  tipo_lugar: 'Indica qué tipo de lugar es.',
  habitaciones_selector: 'Indica cuántas habitaciones tiene el hogar.',
  banos_selector: 'Indica cuántos baños tiene el hogar.',
  areas_comunes: 'Indica las áreas principales que deben atenderse.',
};

function isVisible(element: Element, stopAt?: Element): boolean {
  let node: Element | null = element;
  while (node && node !== stopAt) {
    if (node.hasAttribute('hidden') || node.classList.contains('d-none') || node.getAttribute('aria-hidden') === 'true') return false;
    if (node !== stopAt && window.getComputedStyle(node).display === 'none') return false;
    node = node.parentElement;
  }
  return true;
}

function isNavigableControl(element: Element): element is FieldElement {
  return (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement) &&
    isVisible(element) && !element.disabled && element.type !== 'hidden';
}

function isFieldElement(element: Element): element is FieldElement {
  return element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement;
}

function hasSelectedValue(form: HTMLFormElement, name: string, value: string): boolean {
  return Array.from(form.querySelectorAll(`input[name="${name}"]`)).some((node) =>
    node instanceof HTMLInputElement && node.checked && node.value === value,
  );
}

function cleaningRequiresHouseStructure(form: HTMLFormElement): boolean {
  return hasSelectedValue(form, 'funciones', 'limpieza');
}

function requiresElderCareType(form: HTMLFormElement): boolean {
  return hasSelectedValue(form, 'funciones', 'envejeciente');
}

function elderCareResponsibilitiesIssue(form: HTMLFormElement): boolean {
  if (!requiresElderCareType(form) || !hasSelectedValue(form, 'envejeciente_tipo_cuidado', 'encamado')) return false;
  const responsibilities = Array.from(form.querySelectorAll('input[name="envejeciente_responsabilidades"]'))
    .some((node) => node instanceof HTMLInputElement && node.checked);
  const solo = form.querySelector('input[name="envejeciente_solo_acompanamiento"]');
  return !responsibilities && !(solo instanceof HTMLInputElement && solo.checked);
}

function houseStructureIssue(form: HTMLFormElement): { targetName: HouseStructureTarget; message: string } | null {
  if (!cleaningRequiresHouseStructure(form)) return null;
  const value = (name: string): string => {
    const node = form.querySelector(`[name="${name}"]`);
    return node instanceof HTMLInputElement || node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement
      ? node.value.trim() : '';
  };
  if (!value('tipo_lugar')) return { targetName: 'tipo_lugar', message: HOUSE_STRUCTURE_MESSAGES.tipo_lugar };
  if (!value('habitaciones')) return { targetName: 'habitaciones_selector', message: HOUSE_STRUCTURE_MESSAGES.habitaciones_selector };
  if (!value('banos')) return { targetName: 'banos_selector', message: HOUSE_STRUCTURE_MESSAGES.banos_selector };
  const areas = Array.from(form.querySelectorAll('input[name="areas_comunes"]'));
  if (!areas.some((node) => node instanceof HTMLInputElement && node.checked)) {
    return { targetName: 'areas_comunes', message: HOUSE_STRUCTURE_MESSAGES.areas_comunes };
  }
  return null;
}

function scheduleIssue(form: HTMLFormElement): { targetName: ScheduleTarget; message: string } | null {
  const group = form.querySelector('input[name="modalidad_grupo"]:checked');
  const specific = form.querySelector('[name="modalidad_especifica"]');
  const groupValue = group instanceof HTMLInputElement ? group.value.trim() : '';
  const specificValue = specific instanceof HTMLSelectElement || specific instanceof HTMLInputElement ? specific.value.trim() : '';
  if (!groupValue || !specificValue) return null;

  const value = (name: ScheduleTarget): string => {
    const node = form.querySelector(`[name="${name}"]`);
    return node instanceof HTMLInputElement || node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement
      ? node.value.trim() : '';
  };
  if (groupValue === 'con_dormida') {
    if (!value('horario_dormida_entrada')) return { targetName: 'horario_dormida_entrada', message: MESSAGES.horario_dormida_entrada };
    if (!value('horario_dormida_salida')) return { targetName: 'horario_dormida_salida', message: MESSAGES.horario_dormida_salida };
  } else {
    if (!value('horario_dias_trabajo')) return { targetName: 'horario_dias_trabajo', message: MESSAGES.horario_dias_trabajo };
    if (!value('horario_hora_entrada')) return { targetName: 'horario_hora_entrada', message: MESSAGES.horario_hora_entrada };
    if (!value('horario_hora_salida')) return { targetName: 'horario_hora_salida', message: MESSAGES.horario_hora_salida };
  }

  // `horario` is the canonical value sent to WTForms/backend. Visible values
  // do not make the section complete if this derived field is empty.
  const canonical = form.querySelector('[name="horario"]');
  if (!(canonical instanceof HTMLInputElement) || !canonical.value.trim()) {
    return groupValue === 'con_dormida'
      ? { targetName: 'horario_dormida_entrada', message: 'Confirma el horario de entrada y salida.' }
      : { targetName: 'horario_hora_entrada', message: 'Confirma los días y horas del horario.' };
  }
  return null;
}

function isHouseStructureControl(element: FieldElement): boolean {
  return HOUSE_STRUCTURE_FIELD_NAMES.has(element.name);
}

function draftKey(form: HTMLFormElement): string {
  const scope = String(form.dataset.draftScope || '').trim();
  return scope
    ? `domestica.public-form.${form.id}.${scope}.draft.v4`
    : `domestica.public-form.${form.id}.draft.v4`;
}

function legacyDraftKey(form: HTMLFormElement): string {
  const scope = String(form.dataset.draftScope || '').trim();
  // Old public drafts were keyed only by form id and could belong to a different
  // client. Never restore those unscoped values on a token-based public form.
  return scope
    ? `domestica.public-form.${form.id}.${scope}.draft.v3`
    : `domestica.public-form.${form.id}.draft.v3`;
}

function draftControls(form: HTMLFormElement): FieldElement[] {
  return Array.from(form.elements).filter((element): element is FieldElement =>
    element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement,
  );
}

function isDraftExcluded(element: FieldElement): boolean {
  const name = element.name;
  return !name || DRAFT_EXCLUDED_FIELDS.has(name) || name.startsWith('csrf') ||
    element.getAttribute('data-draft-exclude') === 'true';
}

function serializeDraft(form: HTMLFormElement): DraftEnvelope {
  const values: Record<string, DraftValue> = {};
  const controls = draftControls(form).filter((element) => !isDraftExcluded(element));
  const names = Array.from(new Set(controls.map((element) => element.name)));
  for (const name of names) {
    const named = controls.filter((element) => element.name === name);
    const first = named[0];
    if (first instanceof HTMLInputElement && ['submit', 'button', 'file', 'password'].includes(first.type)) continue;
    if (first instanceof HTMLInputElement && ['checkbox', 'radio'].includes(first.type)) {
      values[name] = named.filter((element): element is HTMLInputElement =>
        element instanceof HTMLInputElement && element.checked,
      ).map((element) => element.value);
    } else if (first instanceof HTMLSelectElement && first.multiple) {
      values[name] = Array.from(first.selectedOptions).map((option) => option.value);
    } else {
      values[name] = first.value;
    }
  }
  return {
    schemaVersion: DRAFT_SCHEMA_VERSION,
    savedAt: Date.now(),
    values,
    serviceValues: form.__serviceDraftState || {},
  };
}

function saveDraft(form: HTMLFormElement): void {
  try { window.sessionStorage.setItem(draftKey(form), JSON.stringify(serializeDraft(form))); } catch (_) { /* storage can be unavailable in private mode */ }
}

function setDraftValues(form: HTMLFormElement, values: Record<string, DraftValue>): void {
  if (values.svc_driver_service && !values.svc_driver_purpose) {
    const legacyValue = Array.isArray(values.svc_driver_service) ? values.svc_driver_service[0] : values.svc_driver_service;
    const legacyPurpose = String(legacyValue || '').toLowerCase();
    values.svc_driver_purpose = /otro/.test(legacyPurpose) ? 'otro'
      : /personal/.test(legacyPurpose) ? 'personal_familiar'
        : /diligencia/.test(legacyPurpose) ? 'transporte_diligencias' : 'traslado_empleados';
    delete values.svc_driver_service;
  }
  for (const [name, rawValue] of Object.entries(values || {})) {
    const expected = Array.isArray(rawValue) ? rawValue : [rawValue];
    const controls = draftControls(form).filter((element) => element.name === name && !isDraftExcluded(element));
    for (const control of controls) {
      if (control instanceof HTMLInputElement && ['checkbox', 'radio'].includes(control.type)) {
        control.checked = expected.includes(control.value);
      } else if (control instanceof HTMLSelectElement && control.multiple) {
        Array.from(control.options).forEach((option) => { option.selected = expected.includes(option.value); });
      } else {
        control.value = expected[0] || '';
      }
    }
  }
}

function dispatchDraftDependencies(form: HTMLFormElement): void {
  const priority = ['modalidad_grupo', 'funciones', 'pasaje_mode', 'habitaciones_selector', 'banos_selector', 'pisos_selector', 'experiencia_visual'];
  const controls = draftControls(form).filter((element) => !isDraftExcluded(element));
  for (const name of priority) {
    controls.filter((element) => element.name === name && (!('checked' in element) || (element as HTMLInputElement).checked))
      .forEach((element) => element.dispatchEvent(new Event('change', { bubbles: true })));
  }
}

function dispatchDraftEvents(form: HTMLFormElement): void {
  draftControls(form).filter((element) => !isDraftExcluded(element)).forEach((element) => {
    const type = element instanceof HTMLInputElement && ['checkbox', 'radio'].includes(element.type)
      ? 'change' : element instanceof HTMLSelectElement ? 'change' : 'input';
    element.dispatchEvent(new Event(type, { bubbles: true }));
  });
}

function restoreDraft(form: HTMLFormElement): DraftRestoreResult {
  try {
    const currentKey = draftKey(form);
    const raw = window.sessionStorage.getItem(currentKey) || window.sessionStorage.getItem(legacyDraftKey(form));
    if (!raw) return { restored: false };
    const envelope = JSON.parse(raw) as DraftEnvelope;
    const version = Number(envelope && envelope.schemaVersion) || LEGACY_DRAFT_SCHEMA_VERSION;
    if (!envelope || ![LEGACY_DRAFT_SCHEMA_VERSION, 3, DRAFT_SCHEMA_VERSION].includes(version) ||
      !Number.isFinite(envelope.savedAt) || envelope.savedAt > Date.now() || Date.now() - envelope.savedAt > DRAFT_MAX_AGE_MS ||
      !envelope.values || typeof envelope.values !== 'object' || Array.isArray(envelope.values)) {
      window.sessionStorage.removeItem(currentKey);
      window.sessionStorage.removeItem(legacyDraftKey(form));
      return { restored: false };
    }
    form.__serviceDraftState = envelope.serviceValues && typeof envelope.serviceValues === 'object' && !Array.isArray(envelope.serviceValues)
      ? envelope.serviceValues : {};
    setDraftValues(form, envelope.values);
    dispatchDraftDependencies(form);
    setDraftValues(form, envelope.values);
    dispatchDraftEvents(form);
    if (version !== DRAFT_SCHEMA_VERSION) {
      window.sessionStorage.setItem(currentKey, JSON.stringify({ ...envelope, schemaVersion: DRAFT_SCHEMA_VERSION }));
      window.sessionStorage.removeItem(legacyDraftKey(form));
    }
    return { restored: Object.keys(envelope.values).length > 0 };
  } catch (_) {
    try {
      window.sessionStorage.removeItem(draftKey(form));
      window.sessionStorage.removeItem(legacyDraftKey(form));
    } catch (_) { /* storage can be unavailable in private mode */ }
    return { restored: false };
  }
}

function messageFor(element: FieldElement): string {
  return MESSAGES[element.id] || MESSAGES[element.name] || 'Completa este dato para continuar.';
}

function applyClientValidation(element: FieldElement): void {
  if (element.id === 'nombre_completo' || element.id === 'nombre_cliente') {
    const value = normalizePersonName(element.value);
    element.setCustomValidity(isValidPersonName(value) ? '' : MESSAGES[element.id]);
  } else if (element.id === 'email_contacto') {
    const value = element.value.trim();
    element.setCustomValidity(value && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? MESSAGES.email_contacto : '');
  } else if (element.id === 'telefono_contacto') {
    const value = element.value.trim();
    const digits = value.replace(/\D/g, '');
    const valid = /^[0-9+\-\s()]{7,20}$/.test(value) && digits.length >= 7;
    element.setCustomValidity(value && !valid ? MESSAGES.telefono_contacto : '');
  }
}

function controlsInCard(form: HTMLFormElement, card: HTMLElement): FieldElement[] {
  return Array.from(card.querySelectorAll('input, select, textarea')).filter((element): element is FieldElement =>
    (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement) &&
    isVisible(element, card) && !element.disabled && element.type !== 'hidden',
  );
}

function isSelectionControl(element: FieldElement): element is HTMLInputElement {
  return element instanceof HTMLInputElement && ['checkbox', 'radio'].includes(element.type);
}

function normalizeSelectionGroupNativeValidity(form: HTMLFormElement): void {
  // A group means "at least one", while HTML required on every checkbox means
  // "all of them". Vue owns the group rule; native validity remains enabled
  // for ordinary inputs, selects, and textareas.
  draftControls(form).forEach((element) => {
    if (isSelectionControl(element) && SELECTION_GROUP_NAMES.has(element.name)) {
      element.required = false;
      element.setAttribute('aria-required', 'false');
    }
    // experiencia is mirrored by the visible experiencia_visual radios. The
    // hidden select must not be a second, native validation gate.
    if (element instanceof HTMLSelectElement && element.name === 'experiencia') {
      element.required = false;
      element.setAttribute('aria-required', 'false');
    }
  });
  SELECTION_GROUPS.forEach(({ name }) => {
    const nodes = Array.from(form.querySelectorAll(`[name="${name}"]`));
    const group = nodes.find((node) => node instanceof HTMLElement)?.closest('.public-field');
    if (group) {
      group.setAttribute('role', 'group');
      group.setAttribute('aria-required', 'true');
    }
  });
}

function collectErrors(form: HTMLFormElement, cards: HTMLElement[]): FormError[] {
  const errors: FormError[] = [];
  const houseActive = cleaningRequiresHouseStructure(form);
  const controls = cards.reduce((all: FieldElement[], card) => all.concat(controlsInCard(form, card)), [])
    .filter((element) => !(houseActive && isHouseStructureControl(element)));
  for (const element of controls) {
    if (isSelectionControl(element) && SELECTION_GROUP_NAMES.has(element.name)) continue;
    applyClientValidation(element);
    if (element.willValidate && !element.checkValidity()) errors.push({ element, message: messageFor(element) });
  }
  for (const { name, message } of SELECTION_GROUPS) {
    if (houseActive && name === 'areas_comunes') continue;
    if (name === 'envejeciente_tipo_cuidado' && !requiresElderCareType(form)) continue;
    const nodes = controls.filter((element) => element.name === name && isSelectionControl(element));
    if (nodes.length && !nodes.some((element) => element instanceof HTMLInputElement && element.checked)) {
      if (!errors.some((error) => error.element === nodes[0])) errors.push({ element: nodes[0], message });
    }
  }
  if (elderCareResponsibilitiesIssue(form)) {
    const target = form.querySelector('input[name="envejeciente_responsabilidades"], input[name="envejeciente_solo_acompanamiento"]');
    if (target instanceof HTMLInputElement) errors.push({ element: target, message: ELDER_RESPONSIBILITIES_MESSAGE });
  }
  const houseIssue = houseStructureIssue(form);
  const houseWrap = form.querySelector('#wrap_home_structure_conditional');
  if (houseIssue && houseWrap instanceof HTMLElement) {
    errors.push({ element: houseWrap, targetName: houseIssue.targetName, message: houseIssue.message });
  }
  const schedule = scheduleIssue(form);
  if (schedule) {
    const target = form.querySelector(`[name="${schedule.targetName}"]`);
    if (target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement) {
      errors.push({ element: target, message: schedule.message });
    }
  }
  return errors.sort((left, right) => {
    const position = left.element.compareDocumentPosition(right.element);
    if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
}

function removeMessages(form: HTMLFormElement): void {
  form.querySelectorAll('.public-vue-field-error').forEach((node) => node.remove());
  form.querySelectorAll('.public-vue-error-target, .public-vue-error-block').forEach((node) => {
    node.classList.remove('public-vue-error-target', 'public-vue-error-block');
  });
}

const NURSE_PROFESSIONAL_TASKS = new Set([
  'administrar_medicamentos', 'administrar_inyecciones', 'tomar_presion', 'signos_vitales', 'curaciones',
]);

function nurseCrossErrors(form: HTMLFormElement): FormError[] {
  const service = form.querySelector('[name="tipo_servicio"]');
  if (!(service instanceof HTMLInputElement) || service.value !== 'ENFERMERA') return [];
  const assistance = form.querySelector('input[name="svc_care_assistance"]:checked');
  const assistanceValue = assistance instanceof HTMLInputElement ? assistance.value : '';
  const tasks = Array.from(form.querySelectorAll('input[name^="profile-responsibility-"]:checked'))
    .filter((node): node is HTMLInputElement => node instanceof HTMLInputElement)
    .map((node) => node.value);
  const hasProfessionalTasks = tasks.some((task) => NURSE_PROFESSIONAL_TASKS.has(task));
  const errors: FormError[] = [];
  if (hasProfessionalTasks && assistanceValue !== 'enfermeria') {
    const target = form.querySelector('.enfermera-assistance-group') || form.querySelector('input[name="svc_care_assistance"]');
    const personCard = Array.from(form.querySelectorAll('.enfermera-person-card')).find((card) =>
      Array.from(card.querySelectorAll('input[name^="profile-responsibility-"]:checked'))
        .some((input) => input instanceof HTMLInputElement && NURSE_PROFESSIONAL_TASKS.has(input.value)));
    const personNumber = personCard ? Array.from(form.querySelectorAll('.enfermera-person-card')).indexOf(personCard) + 1 : 0;
    if (target instanceof HTMLElement) errors.push({
      element: target, validationKey: 'tipo_asistencia',
      message: personNumber ? `Las responsabilidades clínicas de la persona ${personNumber} requieren seleccionar cuidados de enfermería.` : 'Las tareas clínicas requieren seleccionar cuidados de enfermería.',
    });
  }
  const experience = form.querySelector('input[name="experiencia_visual"]:checked');
  const experienceValue = experience instanceof HTMLInputElement ? experience.value : '';
  if ((hasProfessionalTasks || assistanceValue === 'enfermeria') && experienceValue !== 'Enfermera') {
    const target = form.querySelector('.public-experience-grid') || form.querySelector('input[name="experiencia_visual"]');
    if (target instanceof HTMLElement) errors.push({
      element: target, validationKey: 'experiencia_requerida',
      message: 'Selecciona Enfermera para funciones que requieren formación profesional.',
    });
  }
  return errors;
}

function nurseServerErrorStillApplies(form: HTMLFormElement, key: string, crossErrors: FormError[]): boolean {
  const service = form.querySelector('[name="tipo_servicio"]');
  if (!(service instanceof HTMLInputElement) || service.value !== 'ENFERMERA') return false;
  if (crossErrors.some((error) => error.validationKey === key)) return true;
  const countField = form.querySelector('#svc_care_count');
  const profileRoot = form.querySelector('#enfermera-person-profiles');
  const profiles = profileRoot ? Array.from(profileRoot.querySelectorAll('.enfermera-person-card')) : [];
  const count = countField instanceof HTMLInputElement && /^\d+$/.test(countField.value) ? Number(countField.value) : 0;
  const profilesComplete = count >= 1 && profiles.length === count && profiles.every((card) => {
    const age = card.querySelector('input[type="number"]');
    const mobility = card.querySelector('select');
    const selectedConditions = Array.from(card.querySelectorAll('input[name^="profile-condition-"]:checked'));
    const otherResponsibility = card.querySelector<HTMLInputElement | HTMLTextAreaElement>('input[name^="profile-responsibility-"][value="otro"]');
    const otherDescription = card.querySelector<HTMLTextAreaElement>('textarea[name^="profile-responsibilities-other-"]');
    return age instanceof HTMLInputElement && /^\d+$/.test(age.value) && Number(age.value) >= 0 && Number(age.value) <= 120 &&
      mobility instanceof HTMLSelectElement && !!mobility.value && selectedConditions.length > 0 &&
      (!(otherResponsibility instanceof HTMLInputElement && otherResponsibility.checked) || !!otherDescription?.value.trim()) &&
      (!selectedConditions.some((item) => item instanceof HTMLInputElement && item.value === 'ninguna') || selectedConditions.length === 1);
  });
  const assistance = form.querySelector('input[name="svc_care_assistance"]:checked');
  const assistanceValue = assistance instanceof HTMLInputElement ? assistance.value : '';
  const experience = form.querySelector('input[name="experiencia_visual"]:checked');
  const experienceValue = experience instanceof HTMLInputElement ? experience.value : '';
  const tasks = Array.from(form.querySelectorAll('input[name^="profile-responsibility-"]:checked'))
    .filter((node): node is HTMLInputElement => node instanceof HTMLInputElement).map((node) => node.value);
  const hasProfessionalTasks = tasks.some((task) => NURSE_PROFESSIONAL_TASKS.has(task));
  switch (key) {
    case 'cantidad_personas': return !countField || count < 1 || count > 20;
    case 'personas_cuidado': return !profilesComplete;
    case 'tipo_asistencia': return !assistanceValue || (hasProfessionalTasks && assistanceValue !== 'enfermeria');
    case 'experiencia_requerida': return !experienceValue ||
      ((hasProfessionalTasks || assistanceValue === 'enfermeria') && experienceValue !== 'Enfermera');
    case 'condiciones': {
      if (profiles.length) return profiles.some((card) => !card.querySelector('input[name^="profile-condition-"]:checked'));
      const selected = Array.from(form.querySelectorAll('input[name="svc_care_condition"]:checked'))
        .filter((node): node is HTMLInputElement => node instanceof HTMLInputElement);
      return !selected.length || (selected.some((item) => item.value === 'ninguna') && selected.length > 1);
    }
    case 'condicion_otro': return !profiles.length &&
      !!form.querySelector('input[name="svc_care_condition"][value="otra"]:checked') &&
      !String((form.querySelector('#svc_care_condition_other') as HTMLTextAreaElement | null)?.value || '').trim();
    case 'movilidad': return profiles.length
      ? profiles.some((card) => !(card.querySelector('select') as HTMLSelectElement | null)?.value)
      : !String((form.querySelector('#svc_care_mobility') as HTMLSelectElement | null)?.value || '');
    case 'ayuda_higiene': return !profiles.length && !form.querySelector('input[name="svc_care_hygiene"]:checked');
    case 'ayuda_alimentacion': return !profiles.length && !form.querySelector('input[name="svc_care_feeding"]:checked');
    case 'tareas': return false;
    default: return true;
  }
}

function serverValidationErrors(form: HTMLFormElement, errors: FormError[]): FormError[] {
  const crossErrors = nurseCrossErrors(form);
  const result: FormError[] = [...crossErrors];
  form.querySelectorAll<HTMLElement>('#enfermeraServerValidationErrors [data-public-server-error-key]').forEach((item) => {
    const key = item.dataset.publicServerErrorKey || '';
    if (!nurseServerErrorStillApplies(form, key, crossErrors)) {
      item.remove();
      return;
    }
    const targetByKey: Record<string, string> = {
      cantidad_personas: '#svc_care_count', personas_cuidado: '#enfermera-person-profiles',
      tipo_asistencia: '.enfermera-assistance-group', tareas: '#enfermera-person-profiles',
      condiciones: '#enfermera-person-profiles', condicion_otro: '#wrap-svc-care-condition-other',
      ayuda_higiene: '[name="svc_care_hygiene"]', ayuda_alimentacion: '[name="svc_care_feeding"]',
      movilidad: '#enfermera-person-profiles', experiencia_requerida: '.public-experience-grid',
      enf_tareas_otro: '#enfermera-person-profiles', responsabilidad_por_persona: '#enfermera-person-profiles',
    };
    const target = form.querySelector(targetByKey[key] || '#enfermeraServerValidationErrors');
    if (!(target instanceof HTMLElement)) return;
    const message = item.textContent?.trim() || 'Revisa este dato de Enfermería.';
    const alreadyTracked = errors.some((error) => error.element === target ||
      (isFieldElement(error.element) && error.element.closest('.public-field') === target.closest('.public-field')));
    if (!alreadyTracked && !result.some((error) => error.validationKey === key)) {
      result.push({ element: target, message, validationKey: key });
    }
  });
  const serverBox = form.querySelector('#enfermeraServerValidationErrors');
  if (serverBox instanceof HTMLElement && !serverBox.querySelector('[data-public-server-error-key]')) serverBox.remove();
  return result;
}

function reconcileServerNotices(form: HTMLFormElement, errors: FormError[]): void {
  form.querySelectorAll<HTMLElement>('[data-public-wtforms-error]').forEach((notice) => {
    const field = notice.closest('.public-field, .public-input-block, .public-terms-wrap');
    if (!field) return;
    const hasCurrentError = errors.some((error) => error.element === field ||
      (isFieldElement(error.element) && (field.contains(error.element) || error.element.closest('.public-field, .public-input-block, .public-terms-wrap') === field)));
    if (!hasCurrentError) {
      notice.remove();
      field.querySelectorAll('.is-invalid').forEach((node) => node.classList.remove('is-invalid'));
    }
  });
  const formComplete = errors.length === 0;
  document.querySelectorAll<HTMLElement>('.public-flash.danger, .public-flash.error').forEach((notice) => {
    const text = (notice.textContent || '').trim();
    const genericValidation = text === 'Revisa los campos marcados en rojo.';
    const termsValidation = /aceptar los términos y condiciones/i.test(text);
    const termsCheckbox = form.querySelector('#acepta_politica, #acepta_politica_nueva');
    const termsAccepted = termsCheckbox instanceof HTMLInputElement && termsCheckbox.checked;
    if ((genericValidation && formComplete) || (termsValidation && termsAccepted)) notice.remove();
  });
}

function showError(error: FormError): void {
  const target = isFieldElement(error.element)
    ? error.element.closest('.public-field, .public-input-block, .public-terms-wrap') || error.element.parentElement
    : error.element;
  if (!target) return;
  target.classList.add(error.element instanceof HTMLInputElement && ['checkbox', 'radio'].includes(error.element.type) ? 'public-vue-error-block' : 'public-vue-error-target');
  const note = document.createElement('div');
  note.className = 'public-vue-field-error';
  note.setAttribute('role', 'alert');
  note.textContent = error.message;
  target.appendChild(note);
}

function navigationTarget(error: FormError): HTMLElement | null {
  if (!isFieldElement(error.element) && error.targetName) {
    const selectors: Record<HouseStructureTarget, string> = {
      tipo_lugar: '[name="tipo_lugar"]',
      habitaciones_selector: '[name="habitaciones_selector"]',
      banos_selector: '[name="banos_selector"]',
      areas_comunes: '[name="areas_comunes"]',
    };
    const selector = selectors[error.targetName as HouseStructureTarget];
    const target = selector
      ? Array.from(error.element.querySelectorAll(selector))
        .find((node): node is HTMLElement => isNavigableControl(node))
      : undefined;
    if (target) return target;
    if (error.element instanceof HTMLElement && isVisible(error.element)) return error.element;
  }
  if (isNavigableControl(error.element)) return error.element;

  const group = error.element.closest('[role="group"], [role="radiogroup"], [role="groupbox"]');
  const candidates = group ? Array.from(group.querySelectorAll('input, select, textarea')) : [];
  const firstCandidate = candidates.find((node): node is HTMLElement => isNavigableControl(node));
  if (firstCandidate) return firstCandidate;

  const field = error.element.closest('.public-field, .public-input-block, .public-terms-wrap');
  if (!field) return null;
  const fieldControl = Array.from(field.querySelectorAll('input, select, textarea'))
    .find((node): node is HTMLElement => isNavigableControl(node));
  return fieldControl || (field instanceof HTMLElement && isVisible(field) ? field : null);
}

function scrollToNavigableTarget(target: HTMLElement, statusRoot: HTMLElement): void {
  const viewport = window.visualViewport;
  const viewportTop = viewport ? viewport.offsetTop : 0;
  const viewportBottom = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
  const status = statusRoot.querySelector('.public-form-vue-status');
  const bottomInset = status instanceof HTMLElement && getComputedStyle(status).position === 'fixed'
    ? status.getBoundingClientRect().height + 24
    : 24;
  const topInset = 24;
  const rect = target.getBoundingClientRect();
  let delta = 0;
  if (rect.top < viewportTop + topInset) delta = rect.top - (viewportTop + topInset);
  else if (rect.bottom > viewportBottom - bottomInset) delta = rect.bottom - (viewportBottom - bottomInset);
  window.scrollBy({ top: delta, behavior: 'smooth' });
}

function topLevelCards(form: HTMLFormElement): HTMLElement[] {
  const cards: HTMLElement[] = [];
  for (const child of Array.from(form.children)) {
    if (child instanceof HTMLElement && (child.classList.contains('public-client-card') || child.classList.contains('public-form-section'))) cards.push(child);
    if (child instanceof HTMLElement && child.classList.contains('public-form-sections')) {
      cards.push(...Array.from(child.children).filter((node): node is HTMLElement => node instanceof HTMLElement && node.classList.contains('public-form-card')));
    }
    if (child instanceof HTMLElement && child.classList.contains('public-terms-wrap')) cards.push(child);
  }
  return cards;
}

function bootForm(form: HTMLFormElement, root: HTMLElement): void {
  const vue = window.Vue;
  if (!vue) return;
  const cards = topLevelCards(form);
  if (!cards.length) return;
  normalizeSelectionGroupNativeValidity(form);
  const serverPostback = form.dataset.serverPostback === 'true';
  form._restoringDraft = true;
  // On a rejected POST, WTForms and the server-rendered form contain the
  // newest submitted state. A session draft may lag behind the last input
  // event and must never overwrite those values during error recovery.
  const restoredDraft = serverPostback ? { restored: false } : restoreDraft(form);
  form._restoringDraft = false;
  document.dispatchEvent(new CustomEvent('public-form-draft-ready', {
    detail: { formId: form.id, restored: restoredDraft.restored },
  }));
  if (serverPostback) saveDraft(form);
  const state = vue.reactive<FormState>({
    errors: [], restored: restoredDraft.restored, submitting: false, dirty: false,
    serverFailure: Array.from(document.querySelectorAll('.public-flash.danger, .public-flash.error')).some((notice) => {
      const text = (notice.textContent || '').trim();
      return text !== 'Revisa los campos marcados en rojo.' && !/aceptar los términos y condiciones/i.test(text);
    }),
  });
  const sync = (): void => {
    const currentErrors = collectErrors(form, cards);
    const reconciledServerErrors = serverValidationErrors(form, currentErrors);
    state.errors = currentErrors.concat(reconciledServerErrors.filter((serverError) => !currentErrors.some((error) => {
      if (error.element === serverError.element) return true;
      const currentField = error.element.closest('.public-field');
      const serverField = serverError.element.closest('.public-field');
      return !!currentField && currentField === serverField;
    })));
    state.dirty = true;
    reconcileServerNotices(form, state.errors);
  };
  const focusFirst = (preferred?: FormError): void => {
    const error = preferred && 'element' in preferred ? preferred : state.errors[0];
    if (!error) return;
    if (!isFieldElement(error.element) && error.element.id === 'wrap_home_structure_conditional') {
      error.element.classList.remove('d-none', 'is-hidden');
      error.element.setAttribute('aria-hidden', 'false');
      error.element.querySelectorAll('input, select, textarea').forEach((node) => {
        if (node instanceof HTMLInputElement || node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement) {
          node.disabled = false;
        }
      });
    }
    removeMessages(form);
    showError(error);
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        const target = navigationTarget(error);
        if (!target) return;
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        window.setTimeout(() => {
          if (isNavigableControl(target)) {
            target.focus({ preventScroll: true });
            scrollToNavigableTarget(target, root);
          } else if (target instanceof HTMLElement) {
            target.tabIndex = -1;
            target.focus({ preventScroll: true });
            scrollToNavigableTarget(target, root);
          }
        }, 260);
      });
    });
  };
  const complete = vue.computed(() => state.errors.length === 0);
  vue.createApp({
    setup: () => ({ state, complete, focusFirst: () => focusFirst(), focusPending: (error: FormError) => focusFirst(error) }),
    template: '<div class="public-form-vue-ui"><p v-if="state.restored" class="public-field-hint mt-2 mb-0" role="status">Recuperamos los datos no sensibles de tu borrador en este teléfono. Revísalos antes de enviar.</p><div class="public-form-vue-status" :class="{\'is-complete\': complete && !state.serverFailure}"><span class="public-form-vue-status__message" role="status" aria-live="polite"><span class="public-form-vue-status__icon" aria-hidden="true">{{ complete && !state.serverFailure ? \'✓\' : \'!\' }}</span>{{ !complete ? \'Faltan \' + state.errors.length + \' datos pendientes o inválidos\' : state.serverFailure ? \'Los datos están completos. El último envío no se completó; revisa el aviso antes de reenviar.\' : \'Solicitud completa. Ya puedes enviarla.\' }}</span><div v-if="!complete" class="public-form-vue-status__actions"><details class="public-form-vue-status__details"><summary>Ver pendientes</summary><ul><li v-for="(error, index) in state.errors" :key="index"><button type="button" @click="focusPending(error)">{{ error.message }}</button></li></ul></details><button type="button" class="public-form-vue-status__action" @click="focusFirst()">Ir al pendiente</button></div></div></div>',
  }).mount(root);
  let saveTimer = 0;
  const onChange = (): void => {
    sync();
    removeMessages(form);
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => saveDraft(form), 120);
  };
  form.addEventListener('input', onChange);
  form.addEventListener('change', onChange);
  form.addEventListener('submit', (event) => {
    // The bubble-phase bridge opens the conditions modal when it is pending.
    // Do not start first-error navigation in capture phase before that bridge
    // runs: Safari keeps the smooth scroll animation alive while closing.
    // Keep the conditionsForm wording in the source contract while the actual
    // form reference remains the browser-facing object used by the bridge.
    // !conditionsForm.__publicConditionsConfirmed
    if (form.hasAttribute('data-conditions-modal-id') && !form.__publicConditionsConfirmed) return;
    if (state.submitting) { event.preventDefault(); return; }
    sync();
    removeMessages(form);
    if (state.errors.length) { event.preventDefault(); focusFirst(); return; }
    state.serverFailure = false;
    document.querySelectorAll('.public-flash.danger, .public-flash.error').forEach((notice) => {
      const text = (notice.textContent || '').trim();
      if (text !== 'Revisa los campos marcados en rojo.' && !/aceptar los términos y condiciones/i.test(text)) notice.remove();
    });
    state.submitting = true;
    saveDraft(form);
  }, true);
  sync();
}

function boot(): void {
  document.querySelectorAll<HTMLFormElement>('form[data-form-ux="vue"]').forEach((form) => {
    const root = document.getElementById(`${form.id}VueRoot`);
    if (root && !root.dataset.vueReady) { root.dataset.vueReady = '1'; bootForm(form, root); }
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
else boot();
