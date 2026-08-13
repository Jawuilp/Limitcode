# 🔍 Integración de LSP (Language Server Protocol) en Limitcode

Este módulo gestiona la recopilación pasiva de diagnósticos del servidor de lenguaje tras las ediciones del agente para permitir la auto-corrección de errores de sintaxis y tipado.

## Diseño y Diferencias con OpenCode

A diferencia de **OpenCode** (que corre su propio cliente y procesos de servidores LSP en segundo plano de forma independiente), **Limitcode** adopta un enfoque mucho más ligero y nativo de Sublime Text:

1. **Pasivo y sin dependencias**: Se aprovecha el paquete oficial `LSP` (o sublimelinter) que el usuario ya tenga instalado y configurado en su editor.
2. **Uso de buffers existentes**: Consulta directamente los diagnósticos asociados a las vistas abiertas en Sublime Text a través del API nativo (`view.diagnostics()`).

---

## 🚀 Ideas de Mejora Futura (Roadmap Post-v1.0)

Para próximas versiones (v1.1+), se consideran las siguientes mejoras para el colector de diagnósticos:

1. **Expandir la ejecución a otras herramientas de edición**:
   - Actualmente, el colector solo se ejecuta tras llamar a `edit_file`.
   - Se debe expandir para que se ejecute también después de realizar cambios mediante `write_to_file` y `apply_diff`.

2. **Soporte para archivos cerrados (background buffering)**:
   - Si el archivo modificado no está abierto en Sublime Text, el colector no puede encontrar su vista y devuelve una lista vacía de diagnósticos (punto ciego).
   - **Solución propuesta**: Considerar abrir temporalmente el archivo editado en segundo plano (sin enfocar la pestaña ni interrumpir al usuario) para forzar a Sublime Text y a su cliente LSP a procesar y computar los diagnósticos del archivo, recopilar los errores y luego cerrarlo si es necesario.
