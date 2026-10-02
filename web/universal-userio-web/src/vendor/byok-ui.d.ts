// Types for the vendored byok UI module (@bezrabotnyi/byok dist/ui.js).
export declare function renderPresetCards(previews: unknown[], options?: { selectedId?: string; showContext?: boolean; emptyHint?: string }): string
export declare const BYOK_PRESET_CARDS_CSS: string
export declare const BYOK_FORM_FIELDS: Record<string, Array<{ name: string; label: string; placeholder: string; kind: string; options?: string[]; optional?: boolean }>>
export declare function defineByokPresetPicker(customElements?: CustomElementRegistry): void
