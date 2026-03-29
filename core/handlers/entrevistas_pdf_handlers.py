# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import os
import re
import unicodedata

from flask import current_app, redirect, request, send_file, url_for

from decorators import roles_required

from core import legacy_handlers as legacy_h


@roles_required('admin', 'secretaria')
def generar_pdf_entrevista_db(entrevista_id: int):
    # Asegura fpdf2
    try:
        from fpdf import FPDF as _FPDF
        from fpdf.errors import FPDFException
    except Exception:
        return "❌ fpdf2 no está instalado. Ejecuta: pip uninstall -y fpdf && pip install -U fpdf2", 500

    entrevista = legacy_h.Entrevista.query.get_or_404(entrevista_id)

    fila = getattr(entrevista, 'candidata_id', None)
    candidata = legacy_h._get_candidata_safe_by_pk(int(fila)) if fila else None
    if not candidata:
        return "Candidata no encontrada", 404

    respuestas = (
        legacy_h.EntrevistaRespuesta.query
        .filter_by(entrevista_id=entrevista.id)
        .all()
    )
    if not respuestas:
        return "No hay respuestas registradas para esta entrevista.", 404

    pregunta_ids = [r.pregunta_id for r in respuestas if r.pregunta_id]
    preguntas = (
        legacy_h.EntrevistaPregunta.query
        .filter(legacy_h.EntrevistaPregunta.id.in_(pregunta_ids))
        .order_by(legacy_h.EntrevistaPregunta.orden.asc(), legacy_h.EntrevistaPregunta.id.asc())
        .all()
    )

    respuestas_por_pregunta = {r.pregunta_id: (r.respuesta or "").strip() for r in respuestas}
    tipo = (getattr(entrevista, 'tipo', None) or '').strip().lower()

    ref_laborales = (getattr(candidata, 'referencias_laboral', None) or '').strip()
    ref_familiares = (getattr(candidata, 'referencias_familiares', None) or '').strip()

    BRAND = (0, 102, 204)
    FAINT = (120, 120, 120)
    GRID = (210, 210, 210)

    def _ascii_if_needed(s: str, unicode_ok: bool) -> str:
        if unicode_ok:
            return s or ""
        s = s or ""
        nfkd = unicodedata.normalize("NFKD", s)
        return "".join(ch for ch in nfkd if not unicodedata.combining(ch) and ord(ch) < 0x2500)

    def _collapse_ws(s: str) -> str:
        return re.sub(r"[ \t]+", " ", (s or "").strip())

    def _pretty_question(pregunta) -> str:
        for attr in ('enunciado', 'pregunta', 'texto_pregunta', 'texto', 'label', 'etiqueta', 'titulo', 'nombre', 'descripcion'):
            v = (getattr(pregunta, attr, None) or '').strip()
            if v:
                return legacy_h.humanize_pdf_label(v)

        clave = (getattr(pregunta, 'clave', None) or '').strip()
        return legacy_h.humanize_pdf_label(clave) or 'Pregunta'

    def _wrap_unbreakables(s: str, chunk=60) -> str:
        out = []
        for w in (s or "").split(" "):
            if len(w) > chunk:
                out.extend([w[i:i + chunk] for i in range(0, len(w), chunk)])
            else:
                out.append(w)
        return " ".join(out)

    def safe_multicell(pdf, txt, font_name, font_style, font_size, color=None, align="J", line_space=1.2):
        pdf.set_x(pdf.l_margin)
        if color:
            pdf.set_text_color(*color)
        try:
            pdf.set_font(font_name, font_style, font_size)
        except Exception:
            try:
                pdf.set_font("Arial", font_style or "", max(10, int(font_size)))
            except Exception:
                pdf.set_font("Arial", "", 10)

        try:
            pdf.multi_cell(pdf.epw, 7, txt, align=align)
            pdf.ln(line_space)
        except FPDFException:
            txt2 = _wrap_unbreakables(txt, chunk=35)
            pdf.set_font(font_name, "", 10)
            pdf.multi_cell(pdf.epw, 7, txt2, align="L")
            pdf.ln(line_space)

    class InterviewPDF(_FPDF):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._logo_path = None
            self._base_font = "Arial"
            self._unicode_ok = False
            self._has_italic = False
            self._has_bold = False
            self._has_bi = False

        def header(self):
            if self.page_no() == 1:
                if self._logo_path and os.path.exists(self._logo_path):
                    w = 92
                    x = (self.w - w) / 2.0
                    self.image(self._logo_path, x=x, y=10, w=w)
                    y_line = 10 + (w * 0.38)
                    self.set_y(y_line)
                else:
                    self.set_y(18)

                self.set_draw_color(*GRID)
                self.set_line_width(0.6)
                self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
                self.ln(3)

                try:
                    self.set_font(self._base_font, "B", 18 if self._has_bold else 17)
                except Exception:
                    self.set_font("Arial", "B", 18)

                self.set_fill_color(*BRAND)
                self.set_text_color(255, 255, 255)
                self.cell(self.epw, 11, "Entrevista", ln=True, align="C", fill=True)
                self.set_text_color(0, 0, 0)
                self.ln(4)
            else:
                self.set_y(14)
                self.set_draw_color(*GRID)
                self.set_line_width(0.4)
                self.line(self.l_margin, 14, self.w - self.r_margin, 14)
                self.ln(7)

        def footer(self):
            self.set_y(-15)
            try:
                if self._has_italic or self._has_bi:
                    self.set_font(self._base_font, "I", 9)
                else:
                    self.set_font(self._base_font, "", 9)
            except Exception:
                try:
                    self.set_font("Arial", "I", 9)
                except Exception:
                    self.set_font("Arial", "", 9)

            self.set_text_color(*FAINT)
            self.cell(0, 10, f"Página {self.page_no()}/{{nb}}", align="C")

    try:
        pdf = InterviewPDF(format="A4")
        pdf.alias_nb_pages()
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.set_margins(16, 16, 16)
        pdf._logo_path = os.path.join(current_app.root_path, "static", "logo_nuevo.png")

        base_font = "Arial"
        unicode_ok = False
        has_bold = False
        has_italic = False
        has_bi = False

        try:
            font_dir = os.path.join(current_app.root_path, "static", "fonts")
            reg = os.path.join(font_dir, "DejaVuSans.ttf")
            bold = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
            it = os.path.join(font_dir, "DejaVuSans-Oblique.ttf")
            bi = os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf")

            if os.path.exists(reg):
                pdf.add_font("DejaVuSans", "", reg, uni=True)
                base_font = "DejaVuSans"
                unicode_ok = True
            if os.path.exists(bold):
                pdf.add_font("DejaVuSans", "B", bold, uni=True)
                has_bold = True
            if os.path.exists(it):
                pdf.add_font("DejaVuSans", "I", it, uni=True)
                has_italic = True
            if os.path.exists(bi):
                pdf.add_font("DejaVuSans", "BI", bi, uni=True)
                has_bi = True
        except Exception:
            base_font = "Arial"
            unicode_ok = False
            has_bold = True
            has_italic = True
            has_bi = True

        pdf._base_font = base_font
        pdf._unicode_ok = unicode_ok
        pdf._has_bold = has_bold
        pdf._has_italic = has_italic
        pdf._has_bi = has_bi

        pdf.add_page()

        bullet = "• " if unicode_ok else "- "

        try:
            pdf.set_font(base_font, "B" if has_bold else "", 13)
        except Exception:
            pdf.set_font("Arial", "B", 13)

        pdf.set_text_color(*BRAND)
        pdf.cell(0, 9, "📝 Entrevista" if unicode_ok else "Entrevista", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

        for p in preguntas:
            q_txt = _pretty_question(p)
            ans = (respuestas_por_pregunta.get(p.id) or '').strip()

            q_line = _collapse_ws(_ascii_if_needed(q_txt, unicode_ok))
            a_line = _wrap_unbreakables(_collapse_ws(_ascii_if_needed(ans, unicode_ok)), 80)

            safe_multicell(
                pdf,
                (q_line + ":").strip(),
                base_font,
                "B" if has_bold else "",
                12,
                color=(0, 0, 0),
                align="L",
                line_space=1,
            )

            if a_line:
                a_out = (bullet + a_line).strip()
            else:
                a_out = (bullet + "—").strip()

            safe_multicell(
                pdf,
                a_out,
                base_font,
                "",
                12,
                color=BRAND,
                align="J",
                line_space=2,
            )

        pdf.ln(3)

        try:
            pdf.set_font(base_font, "B" if has_bold else "", 13)
        except Exception:
            pdf.set_font("Arial", "B", 13)

        pdf.set_text_color(*BRAND)
        pdf.cell(0, 9, ("📌 " if unicode_ok else "") + "Referencias", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

        try:
            pdf.set_font(base_font, "B" if has_bold else "", 12)
        except Exception:
            pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 7, "Laborales:", ln=True)

        if ref_laborales:
            safe_multicell(
                pdf,
                _wrap_unbreakables(_ascii_if_needed(ref_laborales, unicode_ok), 60),
                base_font,
                "",
                12,
                color=BRAND,
                align="J",
            )
        else:
            safe_multicell(pdf, "No hay referencias laborales.", base_font, "", 12, color=FAINT, align="L")

        try:
            pdf.set_font(base_font, "B" if has_bold else "", 12)
        except Exception:
            pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 7, "Familiares:", ln=True)

        if ref_familiares:
            safe_multicell(
                pdf,
                _wrap_unbreakables(_ascii_if_needed(ref_familiares, unicode_ok), 60),
                base_font,
                "",
                12,
                color=BRAND,
                align="J",
            )
        else:
            safe_multicell(pdf, "No hay referencias familiares.", base_font, "", 12, color=FAINT, align="L")

        raw = pdf.output(dest="S")
        pdf_bytes = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("latin1", "ignore")
        buf = io.BytesIO(pdf_bytes)
        buf.seek(0)

        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"entrevista_{(tipo or 'general')}_{entrevista.id}.pdf"
        )

    except Exception as e:
        current_app.logger.exception("❌ Error interno generando PDF entrevista (DB)")
        return f"Error interno generando PDF: {e}", 500


@roles_required('admin', 'secretaria')
def generar_pdf_entrevista():
    try:
        from fpdf import FPDF as _FPDF
        from fpdf.errors import FPDFException
    except Exception:
        return "❌ fpdf2 no está instalado. Ejecuta: pip uninstall -y fpdf && pip install -U fpdf2", 500

    fila_index = request.args.get('fila', type=int)
    if not fila_index:
        return "Error: falta parámetro fila", 400

    c = legacy_h._get_candidata_by_fila_or_pk(fila_index)
    if not c:
        return "Candidata no encontrada", 404

    texto_entrevista = (getattr(c, "entrevista", None) or "").strip()
    if not texto_entrevista:
        return "No hay entrevista registrada para esa fila", 404

    ref_laborales = (getattr(c, "referencias_laboral", "") or "").strip()
    ref_familiares = (getattr(c, "referencias_familiares", "") or "").strip()

    BRAND = (0, 102, 204)
    FAINT = (120, 120, 120)
    GRID = (210, 210, 210)

    def _ascii_if_needed(s: str, unicode_ok: bool) -> str:
        if unicode_ok:
            return s or ""
        s = s or ""
        nfkd = unicodedata.normalize("NFKD", s)
        return "".join(ch for ch in nfkd if not unicodedata.combining(ch) and ord(ch) < 0x2500)

    def _collapse_ws(s: str) -> str:
        return re.sub(r"[ \t]+", " ", (s or "").strip())

    def _wrap_unbreakables(s: str, chunk=60) -> str:
        out = []
        for w in (s or "").split(" "):
            if len(w) > chunk:
                out.extend([w[i:i + chunk] for i in range(0, len(w), chunk)])
            else:
                out.append(w)
        return " ".join(out)

    def safe_multicell(pdf, txt, font_name, font_style, font_size, color=None, align="J", line_space=1.2):
        pdf.set_x(pdf.l_margin)
        if color:
            pdf.set_text_color(*color)
        try:
            pdf.set_font(font_name, font_style, font_size)
        except Exception:
            try:
                pdf.set_font("Arial", font_style or "", max(10, int(font_size)))
            except Exception:
                pdf.set_font("Arial", "", 10)

        try:
            pdf.multi_cell(pdf.epw, 7, txt, align=align)
            pdf.ln(line_space)
        except FPDFException:
            txt2 = _wrap_unbreakables(txt, chunk=35)
            pdf.set_font(font_name, "", 10)
            pdf.multi_cell(pdf.epw, 7, txt2, align="L")
            pdf.ln(line_space)

    class InterviewPDF(_FPDF):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._logo_path = None
            self._base_font = "Arial"
            self._unicode_ok = False
            self._has_italic = False
            self._has_bold = False
            self._has_bi = False

        def header(self):
            if self.page_no() == 1:
                if self._logo_path and os.path.exists(self._logo_path):
                    w = 92
                    x = (self.w - w) / 2.0
                    self.image(self._logo_path, x=x, y=10, w=w)
                    y_line = 10 + (w * 0.38)
                    self.set_y(y_line)
                else:
                    self.set_y(18)

                self.set_draw_color(*GRID)
                self.set_line_width(0.6)
                self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
                self.ln(3)

                try:
                    self.set_font(self._base_font, "B", 18 if self._has_bold else 17)
                except Exception:
                    self.set_font("Arial", "B", 18)

                self.set_fill_color(*BRAND)
                self.set_text_color(255, 255, 255)
                self.cell(self.epw, 11, "Entrevista", ln=True, align="C", fill=True)
                self.set_text_color(0, 0, 0)
                self.ln(4)
            else:
                self.set_y(14)
                self.set_draw_color(*GRID)
                self.set_line_width(0.4)
                self.line(self.l_margin, 14, self.w - self.r_margin, 14)
                self.ln(7)

        def footer(self):
            self.set_y(-15)
            try:
                if self._has_italic or self._has_bi:
                    self.set_font(self._base_font, "I", 9)
                else:
                    self.set_font(self._base_font, "", 9)
            except Exception:
                try:
                    self.set_font("Arial", "I", 9)
                except Exception:
                    self.set_font("Arial", "", 9)

            self.set_text_color(*FAINT)
            self.cell(0, 10, f"Página {self.page_no()}/{{nb}}", align="C")

    try:
        pdf = InterviewPDF(format="A4")
        pdf.alias_nb_pages()
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.set_margins(16, 16, 16)
        pdf._logo_path = os.path.join(current_app.root_path, "static", "logo_nuevo.png")

        base_font = "Arial"
        unicode_ok = False
        has_bold = False
        has_italic = False
        has_bi = False

        try:
            font_dir = os.path.join(current_app.root_path, "static", "fonts")
            reg = os.path.join(font_dir, "DejaVuSans.ttf")
            bold = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
            it = os.path.join(font_dir, "DejaVuSans-Oblique.ttf")
            bi = os.path.join(font_dir, "DejaVuSans-BoldOblique.ttf")

            if os.path.exists(reg):
                pdf.add_font("DejaVuSans", "", reg, uni=True)
                base_font = "DejaVuSans"
                unicode_ok = True
            if os.path.exists(bold):
                pdf.add_font("DejaVuSans", "B", bold, uni=True)
                has_bold = True
            if os.path.exists(it):
                pdf.add_font("DejaVuSans", "I", it, uni=True)
                has_italic = True
            if os.path.exists(bi):
                pdf.add_font("DejaVuSans", "BI", bi, uni=True)
                has_bi = True
        except Exception:
            base_font = "Arial"
            unicode_ok = False
            has_bold = True
            has_italic = True
            has_bi = True

        pdf._base_font = base_font
        pdf._unicode_ok = unicode_ok
        pdf._has_bold = has_bold
        pdf._has_italic = has_italic
        pdf._has_bi = has_bi

        pdf.add_page()

        bullet = "• " if unicode_ok else "- "

        try:
            pdf.set_font(base_font, "B" if has_bold else "", 13)
        except Exception:
            pdf.set_font("Arial", "B", 13)

        pdf.set_text_color(*BRAND)
        pdf.cell(0, 9, "📝 Entrevista" if unicode_ok else "Entrevista", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

        for raw in (texto_entrevista or "").splitlines():
            line = _collapse_ws(_ascii_if_needed(raw, unicode_ok))
            if ":" in line:
                q, a = line.split(":", 1)
                q = _collapse_ws(legacy_h.humanize_pdf_label(q))
                a = _collapse_ws(a)

                safe_multicell(
                    pdf,
                    (q + ":").strip(),
                    base_font,
                    "B" if has_bold else "",
                    12,
                    color=(0, 0, 0),
                    align="L",
                    line_space=1,
                )

                ans = _wrap_unbreakables(a, 60)
                ans = (bullet + ans) if ans else ans
                safe_multicell(pdf, ans, base_font, "", 12, color=BRAND, align="J", line_space=2)
            else:
                safe_multicell(
                    pdf,
                    _wrap_unbreakables(line, 60),
                    base_font,
                    "",
                    12,
                    color=(0, 0, 0),
                    align="J",
                    line_space=1.5,
                )

        pdf.ln(3)

        try:
            pdf.set_font(base_font, "B" if has_bold else "", 13)
        except Exception:
            pdf.set_font("Arial", "B", 13)

        pdf.set_text_color(*BRAND)
        pdf.cell(0, 9, ("📌 " if unicode_ok else "") + "Referencias", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

        try:
            pdf.set_font(base_font, "B" if has_bold else "", 12)
        except Exception:
            pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 7, "Laborales:", ln=True)

        if ref_laborales:
            safe_multicell(
                pdf,
                _wrap_unbreakables(_ascii_if_needed(ref_laborales, unicode_ok), 60),
                base_font,
                "",
                12,
                color=BRAND,
                align="J",
            )
        else:
            safe_multicell(pdf, "No hay referencias laborales.", base_font, "", 12, color=FAINT, align="L")

        try:
            pdf.set_font(base_font, "B" if has_bold else "", 12)
        except Exception:
            pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 7, "Familiares:", ln=True)

        if ref_familiares:
            safe_multicell(
                pdf,
                _wrap_unbreakables(_ascii_if_needed(ref_familiares, unicode_ok), 60),
                base_font,
                "",
                12,
                color=BRAND,
                align="J",
            )
        else:
            safe_multicell(pdf, "No hay referencias familiares.", base_font, "", 12, color=FAINT, align="L")

        raw = pdf.output(dest="S")
        pdf_bytes = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("latin1", "ignore")
        buf = io.BytesIO(pdf_bytes)
        buf.seek(0)

        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"entrevista_candidata_{fila_index}.pdf"
        )

    except Exception as e:
        current_app.logger.exception("❌ Error interno generando PDF")
        return f"Error interno generando PDF: {e}", 500


@roles_required('admin', 'secretaria')
def generar_pdf_entrevista_nueva_db(entrevista_id: int):
    return generar_pdf_entrevista_db(entrevista_id)


@roles_required('admin', 'secretaria')
def generar_pdf_ultima_entrevista_candidata(fila: int):
    EntrevistaModel = globals().get('Entrevista')
    if EntrevistaModel is None:
        return "❌ No se encontró el modelo 'Entrevista' en el proyecto.", 500

    try:
        q = legacy_h.db.session.query(EntrevistaModel)
        if hasattr(EntrevistaModel, 'candidata_id'):
            q = q.filter(EntrevistaModel.candidata_id == fila)
        elif hasattr(EntrevistaModel, 'fila'):
            q = q.filter(EntrevistaModel.fila == fila)
        elif hasattr(EntrevistaModel, 'candidata_fila'):
            q = q.filter(EntrevistaModel.candidata_fila == fila)

        if hasattr(EntrevistaModel, 'actualizada_en'):
            q = q.order_by(EntrevistaModel.actualizada_en.desc())
        elif hasattr(EntrevistaModel, 'creada_en'):
            q = q.order_by(EntrevistaModel.creada_en.desc())
        else:
            q = q.order_by(EntrevistaModel.id.desc())

        last = q.first()
    except Exception:
        last = None

    if not last:
        return "No hay entrevistas nuevas registradas para esa candidata", 404

    return redirect(url_for('generar_pdf_entrevista_db', entrevista_id=int(getattr(last, 'id', 0))))


# Referencia global para mantener el comportamiento de globals().get("Entrevista")
Entrevista = legacy_h.Entrevista
