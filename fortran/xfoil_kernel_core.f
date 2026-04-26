C***********************************************************************
C  Shared non-interactive XFOIL kernel routines.
C
C  These routines are the first modernization boundary around XFOIL COMMON
C  state. They deliberately keep the original COMMON-backed numerical state
C  intact, but centralize driver/session setup and solve operations so later
C  state refactors have one smaller surface to change.
C***********************************************************************
      SUBROUTINE XK_DISABLE_PLOTTING
      INCLUDE 'XFOIL.INC'
C
      OPEN(99,FILE='/dev/null',STATUS='UNKNOWN')
      IDEV = 0
      IDEVRP = 0
      LPLOT = .FALSE.
      LPACC = .FALSE.
      LPPSHO = .FALSE.
      LCURS = .FALSE.
C
      RETURN
      END


      SUBROUTINE XK_DEFAULT_CASE(MAXALF,AIRFOIL_TYPE,COORDINATE_FILE,
     &  NACA_CODE,VISCOUS,REYNOLDS_NUMBER,MACH_NUMBER,NCRIT_TOP,
     &  NCRIT_BOTTOM,XTR_TOP,XTR_BOTTOM,ITMAX,PANEL_COUNT,
     &  PANEL_AIRFOIL,N_ALPHA,ALPHA_DEG)
      INTEGER MAXALF
      CHARACTER*16 AIRFOIL_TYPE
      CHARACTER*256 COORDINATE_FILE
      INTEGER NACA_CODE
      INTEGER ITMAX
      INTEGER PANEL_COUNT
      INTEGER N_ALPHA
      REAL REYNOLDS_NUMBER
      REAL MACH_NUMBER
      REAL NCRIT_TOP
      REAL NCRIT_BOTTOM
      REAL XTR_TOP
      REAL XTR_BOTTOM
      REAL ALPHA_DEG(MAXALF)
      LOGICAL VISCOUS
      LOGICAL PANEL_AIRFOIL
      INTEGER I
C
      AIRFOIL_TYPE = 'naca'
      COORDINATE_FILE = ' '
      NACA_CODE = 12
      VISCOUS = .FALSE.
      REYNOLDS_NUMBER = 0.0
      MACH_NUMBER = 0.0
      NCRIT_TOP = 9.0
      NCRIT_BOTTOM = 9.0
      XTR_TOP = 1.0
      XTR_BOTTOM = 1.0
      ITMAX = 50
      PANEL_COUNT = 160
      PANEL_AIRFOIL = .TRUE.
      N_ALPHA = 0
      DO I = 1, MAXALF
        ALPHA_DEG(I) = 0.0
      ENDDO
C
      RETURN
      END


      SUBROUTINE XK_VALIDATE_CASE(MAXALF,N_ALPHA,ITMAX,PANEL_COUNT,
     &  CASE_OK,MESSAGE)
      INTEGER MAXALF
      INTEGER N_ALPHA
      INTEGER ITMAX
      INTEGER PANEL_COUNT
      LOGICAL CASE_OK
      CHARACTER*(*) MESSAGE
C
      CASE_OK = .TRUE.
      MESSAGE = ' '
      IF(N_ALPHA .LE. 0) THEN
        CASE_OK = .FALSE.
        MESSAGE = 'no alpha values requested'
      ELSEIF(N_ALPHA .GT. MAXALF) THEN
        CASE_OK = .FALSE.
        MESSAGE = 'too many alpha values requested'
      ELSEIF(ITMAX .LE. 0) THEN
        CASE_OK = .FALSE.
        MESSAGE = 'itmax must be positive'
      ELSEIF(PANEL_COUNT .LE. 1) THEN
        CASE_OK = .FALSE.
        MESSAGE = 'panel_count must be greater than one'
      ENDIF
C
      RETURN
      END


      SUBROUTINE XK_RESET_BOUNDARY_LAYER_STATE
      INCLUDE 'XFOIL.INC'
C
      LVCONV = .FALSE.
      LBLINI = .FALSE.
      LWAKE = .FALSE.
      LIPAN = .FALSE.
C
      RETURN
      END


      SUBROUTINE XK_LOAD_GEOMETRY(AIRFOIL_TYPE,COORDINATE_FILE,
     &  NACA_CODE,PANEL_COUNT,PANEL_AIRFOIL,CASE_OK,MESSAGE)
      INCLUDE 'XFOIL.INC'
      CHARACTER*(*) AIRFOIL_TYPE
      CHARACTER*(*) COORDINATE_FILE
      CHARACTER*(*) MESSAGE
      INTEGER NACA_CODE
      INTEGER PANEL_COUNT
      INTEGER ITYPE
      LOGICAL PANEL_AIRFOIL
      LOGICAL CASE_OK
C
      CASE_OK = .TRUE.
      MESSAGE = ' '
      NPAN = PANEL_COUNT
C
      IF(AIRFOIL_TYPE(1:4) .EQ. 'naca' .OR.
     &   AIRFOIL_TYPE(1:4) .EQ. 'NACA') THEN
        IF(NACA_CODE .LE. 0) THEN
          CASE_OK = .FALSE.
          MESSAGE = 'naca_code must be positive'
          RETURN
        ENDIF
        CALL NACA(NACA_CODE)
      ELSEIF(AIRFOIL_TYPE(1:11) .EQ. 'coordinates' .OR.
     &       AIRFOIL_TYPE(1:11) .EQ. 'COORDINATES') THEN
        IF(COORDINATE_FILE(1:1) .EQ. ' ') THEN
          CASE_OK = .FALSE.
          MESSAGE = 'coordinate_file is required'
          RETURN
        ENDIF
        CALL LOAD(COORDINATE_FILE,ITYPE)
        IF(ITYPE .LE. 0 .OR. NB .LE. 0) THEN
          CASE_OK = .FALSE.
          MESSAGE = 'could not load coordinate airfoil'
          RETURN
        ENDIF
        IF(PANEL_AIRFOIL) THEN
          CALL PANGEN(.TRUE.)
        ELSE
          CALL ABCOPY(.TRUE.)
        ENDIF
      ELSE
        CASE_OK = .FALSE.
        MESSAGE = 'unsupported airfoil_type'
        RETURN
      ENDIF
C
      IF(N .LE. 0) THEN
        CASE_OK = .FALSE.
        MESSAGE = 'no paneled airfoil available'
      ENDIF
C
      RETURN
      END


      SUBROUTINE XK_APPLY_OPTIONS(VISCOUS,REYNOLDS_NUMBER,
     &  MACH_NUMBER,NCRIT_TOP,NCRIT_BOTTOM,XTR_TOP,XTR_BOTTOM,
     &  RESET_STATE)
      INCLUDE 'XFOIL.INC'
      REAL REYNOLDS_NUMBER
      REAL MACH_NUMBER
      REAL NCRIT_TOP
      REAL NCRIT_BOTTOM
      REAL XTR_TOP
      REAL XTR_BOTTOM
      LOGICAL VISCOUS
      LOGICAL RESET_STATE
C
      LVISC = VISCOUS
      RETYP = 1
      MATYP = 1
      REINF1 = REYNOLDS_NUMBER
      MINF1 = MACH_NUMBER
      ACRIT(1) = NCRIT_TOP
      ACRIT(2) = NCRIT_BOTTOM
      XSTRIP(1) = XTR_TOP
      XSTRIP(2) = XTR_BOTTOM
      IF(RESET_STATE) CALL XK_RESET_BOUNDARY_LAYER_STATE
C
      RETURN
      END


      SUBROUTINE XK_PREPARE_OPERATING_POINT
      INCLUDE 'XFOIL.INC'
C
      CALL MRCL(1.0,MINF_CL,REINF_CL)
      CALL COMSET
C
      RETURN
      END


      SUBROUTINE XK_SOLVE_ALPHA_POINT(ALPHA_VALUE,ITERATION_LIMIT)
      INCLUDE 'XFOIL.INC'
      REAL ALPHA_VALUE
      INTEGER ITERATION_LIMIT
C
      LALFA = .TRUE.
      ADEG = ALPHA_VALUE
      ALFA = DTOR*ADEG
      QINF = 1.0
C
      CALL SPECAL
      IF(ABS(ALFA-AWAKE) .GT. 1.0E-5) LWAKE = .FALSE.
      IF(ABS(ALFA-AVISC) .GT. 1.0E-5) LVCONV = .FALSE.
      IF(ABS(MINF-MVISC) .GT. 1.0E-5) LVCONV = .FALSE.
C
      IF(LVISC) THEN
        CALL VISCAL(ITERATION_LIMIT+5)
      ELSE
        CALL CDCALC
        LVCONV = .TRUE.
        RMSBL = 0.0
        XOCTR(1) = XSTRIP(1)
        XOCTR(2) = XSTRIP(2)
        TFORCE(1) = .FALSE.
        TFORCE(2) = .FALSE.
      ENDIF
C
      ADEG = ALFA/DTOR
C
      RETURN
      END
