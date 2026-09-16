"""Tela de ajuda (F1): mapa de teclado e visão geral dos endpoints."""

from __future__ import annotations

from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

TEXTO_AJUDA = """\
NAVEGAÇÃO
  Tab / Shift+Tab     move o foco entre campos
  Setas / Enter       navega e confirma em listas e menus
  Clique do mouse      funciona em qualquer botão, item de lista ou tabela

TECLAS GLOBAIS
  F1                  esta tela de ajuda
  L                   log de requisições/respostas
  F10                 sair do programa (com confirmação)

DENTRO DE UM FORMULÁRIO
  F5                  executa a chamada (equivalente a clicar em "Executar")
  Esc                 volta para a tela anterior, descartando o formulário

DENTRO DE UMA TELA DE RESULTADO
  c ou Ctrl+C (com um campo em foco)   copia o valor original para a área de
                                        transferência
  Esc                                  fecha o resultado

MENU PRINCIPAL
  Importar          /importar, /enviarParaEnvioInteligente (com botão para
                    escolher o arquivo pelo explorador nativo do sistema)
  Modelos           GET /api/modelos — clique num modelo para criar um
                    documento a partir dele (POST /api/documento/from-template),
                    com um campo por variável do modelo
  Assinatura        POST/DELETE /api/assinatura, GET status
  Documentos        GET /api/documentos, GET download
  Fluxo Completo    importar + solicitar assinatura em sequência
  Perfil            troca a instância/token ativos sem reiniciar
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [("escape", "fechar", "Fechar")]

    def compose(self):
        with Container(classes="dos-window"):
            yield Static("Ajuda", classes="dos-window-title")
            with VerticalScroll():
                yield Static(TEXTO_AJUDA)
            yield Static("Esc para fechar", classes="result-hint")

    def action_fechar(self) -> None:
        self.dismiss(None)
