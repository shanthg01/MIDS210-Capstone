import { Box } from '@mui/material';
import type { ReactNode } from 'react';
import Tooltip, { type TooltipProps } from '@mui/material/Tooltip';

interface Props {
  title: string;
  children: ReactNode;
  placement?: TooltipProps['placement'];
}

export default function DefinitionTooltip({ title, children, placement = 'top' }: Props) {
  return (
    <Tooltip title={title} placement={placement}>
      <Box
        component="span"
        sx={{ cursor: 'help', borderBottom: '1px dotted', borderColor: 'text.disabled' }}
      >
        {children}
      </Box>
    </Tooltip>
  );
}
