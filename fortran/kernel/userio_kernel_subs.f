C Extracted kernel source. Original source: vendor/xfoil/src/userio.f
C Contains only subroutines needed by the non-interactive kernel driver.
      SUBROUTINE GETFLT(INPUT,A,N,ERROR)
      CHARACTER*(*) INPUT
      REAL A(*)
      LOGICAL ERROR
C----------------------------------------------------------
C     Parses character string INPUT into an array
C     of real numbers returned in A(1...N)
C
C     Will attempt to extract no more than N numbers, 
C     unless N = 0, in which case all numbers present
C     in INPUT will be extracted.
C
C     N returns how many numbers were actually extracted.
C----------------------------------------------------------
      CHARACTER*130 REC
      CHARACTER*1 TAB
C
      TAB = CHAR(9)
C
C---- only first 128 characters in INPUT will be parsed
      ILEN = MIN( LEN(INPUT) , 128 )
      ILENP = ILEN + 2
C
C---- put input into local work string (which will be munched)
      REC(1:ILENP) = INPUT(1:ILEN) // ' ,'
C
C---- ignore everything after a "!" character
      K = INDEX(REC,'!')
      IF(K.GT.0) REC(1:ILEN) = REC(1:K-1)
C
C---- change tabs to spaces
 5    K = INDEX(REC(1:ILEN),TAB)
      IF(K.GT.0) THEN
       REC(K:K) = ' '
       GO TO 5
      ENDIF
C
      NINP = N
C
C---- count up how many numbers are to be extracted
      N = 0
      K = 1
      DO 10 IPASS=1, ILEN
C------ search for next space or comma starting with current index K
        KSPACE = INDEX(REC(K:ILENP),' ') + K - 1
        KCOMMA = INDEX(REC(K:ILENP),',') + K - 1
C
        IF(K.EQ.KSPACE) THEN
C------- just skip this space
         K = K+1
         GO TO 9
        ENDIF
C
        IF(K.EQ.KCOMMA) THEN
C------- comma found.. increment number count and keep looking
         N = N+1
         K = K+1
         GO TO 9
        ENDIF
C
C------ neither space nor comma found, so we ran into a number...
C-    ...increment number counter and keep looking after next space or comma
        N = N+1
        K = MIN(KSPACE,KCOMMA) + 1
C
  9     IF(K.GE.ILEN) GO TO 11
 10   CONTINUE
C
C---- decide on how many numbers to read, and go ahead and read them
 11   IF(NINP.GT.0) N = MIN( N, NINP )
      READ(REC(1:ILEN),*,ERR=20) (A(I),I=1,N)
      ERROR = .FALSE.
      RETURN
C
C---- bzzzt !!!
 20   CONTINUE
ccc   WRITE(99,*) 'GETFLT: String-to-integer conversion error.'
      N = 0
      ERROR = .TRUE.
      RETURN
      END ! GETFLT




      SUBROUTINE STRIP(STRING,NS)
      CHARACTER*(*) STRING
C----------------------------------------------------
C     Strips leading blanks off STRING and returns 
C     length NS of non-blank part.
C----------------------------------------------------
      NLEN = LEN(STRING)
C
C---- find last non-blank character
      DO K2 = NLEN, 1, -1
        IF(STRING(K2:K2).NE.' ') GO TO 11
      ENDDO
      K2 = 0
   11 CONTINUE
C
C---- find first non-blank character
      DO K1 = 1, K2
        IF(STRING(K1:K1).NE.' ') GO TO 21
      ENDDO
   21 CONTINUE
C
C---- number of non-blank characters
      NS = K2 - K1 + 1
      IF(NS.EQ.0) RETURN
C
C---- shift STRING so first character is non-blank
      STRING(1:NS) = STRING(K1:K2)
C
C---- pad tail of STRING with blanks
      DO K = NS+1, NLEN
        STRING(K:K) = ' '
      ENDDO
C
      RETURN
      END






