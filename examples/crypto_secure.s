	.file	"tmpuht5uk2f.c"
	.intel_syntax noprefix
	.text
	.p2align 4
	.globl	_force_create
	.type	_force_create, @function
_force_create:
	endbr64
	sub	rsp, 8
	mov	esi, 4096
	mov	edi, 16
	call	aligned_alloc@PLT
	mov	rdx, rax
	test	rax, rax
	je	.L1
	lea	rsi, 16[rax]
	pxor	xmm0, xmm0
	mov	ecx, 510
	movaps	XMMWORD PTR [rax], xmm0
	mov	rdi, rsi
	xor	eax, eax
	rep stosq
.L1:
	mov	rax, rdx
	add	rsp, 8
	ret
	.size	_force_create, .-_force_create
	.p2align 4
	.globl	_force_alloc
	.type	_force_alloc, @function
_force_alloc:
	endbr64
	lea	rax, -1[rsi]
	cmp	rax, 4079
	ja	.L11
	test	rdi, rdi
	je	.L11
	mov	rax, QWORD PTR [rdi]
	add	rax, 15
	and	rax, -16
	cmp	rax, 4080
	ja	.L11
	mov	rdx, rsi
	mov	esi, 4080
	xor	ecx, ecx
	sub	rsi, rax
	cmp	rsi, rdx
	jb	.L14
	lea	rcx, 16[rdi+rax]
	add	rax, rdx
	sub	rsp, 8
	xor	esi, esi
	mov	QWORD PTR [rdi], rax
	mov	rdi, rcx
	call	memset@PLT
	add	rsp, 8
	ret
	.p2align 4,,10
	.p2align 3
.L11:
	xor	ecx, ecx
.L14:
	mov	rax, rcx
	ret
	.size	_force_alloc, .-_force_alloc
	.p2align 4
	.globl	_force_reset
	.type	_force_reset, @function
_force_reset:
	endbr64
	test	rdi, rdi
	je	.L27
	push	rbx
	mov	rdx, QWORD PTR [rdi]
	mov	rbx, rdi
	test	rdx, rdx
	jne	.L30
	add	QWORD PTR 8[rbx], 1
	mov	QWORD PTR [rbx], 0
	pop	rbx
	ret
	.p2align 4,,10
	.p2align 3
.L30:
	lea	rdi, 16[rdi]
	xor	esi, esi
	call	memset@PLT
	add	QWORD PTR 8[rbx], 1
	mov	QWORD PTR [rbx], 0
	pop	rbx
	ret
	.p2align 4,,10
	.p2align 3
.L27:
	ret
	.size	_force_reset, .-_force_reset
	.p2align 4
	.globl	_force_destroy
	.type	_force_destroy, @function
_force_destroy:
	endbr64
	test	rdi, rdi
	je	.L31
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L31:
	ret
	.size	_force_destroy, .-_force_destroy
	.ident	"GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"
	.section	.note.GNU-stack,"",@progbits
	.section	.note.gnu.property,"a"
	.align 8
	.long	1f - 0f
	.long	4f - 1f
	.long	5
0:
	.string	"GNU"
1:
	.align 8
	.long	0xc0000002
	.long	3f - 2f
2:
	.long	0x3
3:
	.align 8
4:
