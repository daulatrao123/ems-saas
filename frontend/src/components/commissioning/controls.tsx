"use client";
import { Children, cloneElement, isValidElement, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { btn, input, tone } from "../ops/DashboardHeader";

export const fieldStyle = `${input} w-full min-w-0 rounded-sm min-h-10`;
export function Field({ id, label, ...props }: InputHTMLAttributes<HTMLInputElement> & { id: string; label: string }) {
  return <label htmlFor={id} className="block min-w-0 text-xs text-gray-400"><span className="mb-2 block">{label}</span><input {...props} id={id} data-testid={id} className={fieldStyle} /></label>;
}
export function SelectField({ id, label, children, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { id: string; label: string; children: ReactNode }) {
  return <label htmlFor={id} className="block min-w-0 text-xs text-gray-400"><span className="mb-2 block">{label}</span><select {...props} id={id} data-testid={id} className={fieldStyle}>{Children.map(children, child => isValidElement<Record<string, unknown>>(child) ? cloneElement(child, { "data-testid": `${id}-option-${String(child.props.value ?? child.props.children)}` }) : child)}</select></label>;
}
export function Section({ id, number, title, children }: { id: string; number: string; title: string; children: ReactNode }) {
  return <section data-testid={id} className="min-w-0 border-t border-[#2a3646] py-7"><h2 data-testid={`${id}-heading`} className="mb-6 flex items-baseline gap-3 text-base font-semibold text-white md:text-lg"><span className="font-mono text-xs text-cyan-400">{number}</span>{title}</h2>{children}</section>;
}
export function Actions({ id, dirty, disabled, reset, label, allowInitial = false }: { id: string; dirty: boolean; disabled: boolean; reset: () => void; label: string; allowInitial?: boolean }) {
  return <div className="mt-5 flex flex-wrap items-center gap-3"><button data-testid={`${id}-save`} type="submit" disabled={disabled || (!dirty && !allowInitial)} className={`${btn} ${tone.cyan}`}>{label}</button><button data-testid={`${id}-discard`} type="button" disabled={!dirty} onClick={reset} className={`${btn} ${tone.gray}`}>Discard edits</button>{dirty && <span data-testid={`${id}-dirty`} className="text-xs text-amber-300">Unsaved changes</span>}</div>;
}
export function Alert({ id, children }: { id: string; children: ReactNode }) {
  return <p data-testid={id} role="alert" className="my-3 border-l-2 border-amber-400 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-200">{children}</p>;
}