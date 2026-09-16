import { useMemo, useState } from "react";
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography } from "@mui/material";
import { Form as MuiForm } from "@rjsf/mui";
import { SchemaForm } from "@flyball/react";
import type { DashboardWidget } from "@flyball/client";
import { widgetKind } from "../widgets/registry.js";
import { useBindings } from "./context.js";

/** The widget's title and its kind's config form, live; Apply hands back the edited widget. Mounted per widget (keyed by the caller). */
export function ConfigureDialog({ widget, onClose, onApply }: { widget: DashboardWidget; onClose(): void; onApply(widget: DashboardWidget): void }) {
  const bindings = useBindings();
  const kind = widgetKind(widget.kind);
  const [title, setTitle] = useState(widget.title ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(widget.config);
  // The schema may depend on the value (an actuator's commands follow the actuator picked), so it is rebuilt as the form changes.
  const schema = useMemo(() => kind?.configSchema(bindings, config), [kind, bindings, config]);
  const placeholder = kind?.titleFor?.(config, bindings) ?? kind?.label ?? widget.kind;
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm" data-testid="configure-dialog">
      <DialogTitle>
        Configure {kind?.label ?? widget.kind}
        <Typography variant="body2" color="text.secondary" component="span" sx={{ ml: 1.5 }}>
          {widget.id}
        </Typography>
      </DialogTitle>
      <DialogContent>
        <TextField label="Title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={placeholder} helperText="Blank: the widget names itself." fullWidth sx={{ mt: 1.5, mb: 1.5 }} />
        {schema && kind ? (
          <SchemaForm schema={schema} value={config} onChange={setConfig} uiSchema={kind.uiSchema} form={MuiForm} idPrefix={`cfg-${widget.id}`} />
        ) : (
          <Typography color="text.secondary">Unknown widget kind: nothing to configure.</Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => onApply({ ...widget, title: title.trim() || null, config })} data-testid="configure-apply">
          Apply
        </Button>
      </DialogActions>
    </Dialog>
  );
}
