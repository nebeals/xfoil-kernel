C***********************************************************************
C  Fail-fast prompt stubs for the non-interactive kernel driver.
C
C  The kernel path should never ask stdin for missing values. These stubs
C  preserve link compatibility with legacy routines that still contain prompt
C  branches, while turning any reachable prompt into a structured failure in
C  the driver transcript instead of blocking the worker.
C***********************************************************************
      SUBROUTINE ASKI(PROMPT,IINPUT)
      CHARACTER*(*) PROMPT
      INTEGER IINPUT
C
      WRITE(*,*) 'XK_ERROR interactive integer prompt requested: ',
     &  PROMPT
      STOP 4
      END

      SUBROUTINE ASKS(PROMPT,INPUT)
      CHARACTER*(*) PROMPT
      CHARACTER*(*) INPUT
C
      WRITE(*,*) 'XK_ERROR interactive string prompt requested: ',
     &  PROMPT
      STOP 4
      END
